"""Targeted product lookup: one bounded search and fresh evidence, no recommendation agents."""
from sqlalchemy import select
import re

from app.context.service import conversation_context_service
from app.db.session import SessionLocal
from app.models.truth_v3 import CatalogEntity, EvidenceClaim, SourceDocument, GpuSpec, CpuSpec, AIModel, ModelVariant
from app.schemas.recommendation import Recommendation
from app.services.events import emit
from app.services.hardware_intent import sku_requests, matches_sku
from app.verification import TruthVerifier
from app.verification.repository import SqlAlchemyTruthRepository

FIELD_LABELS = {
    "gpu.architecture": "架构", "gpu.vram_gib": "显存容量", "gpu.memory_type": "显存类型",
    "gpu.board_power_w": "板卡功耗", "gpu.memory_bus_width_bit": "显存位宽",
    "gpu.memory_bandwidth_gb_s": "显存带宽", "gpu.boost_clock_mhz": "加速频率",
    "cpu.architecture": "架构", "cpu.cores_total": "核心数", "cpu.threads": "线程数",
    "cpu.socket": "插槽", "cpu.base_power_w": "基础功耗", "cpu.max_power_w": "最大功耗",
    "cpu.integrated_gpu": "核显", "cpu.boost_clock_mhz": "加速频率",
    "model.total_parameters": "参数量", "model.context_length_tokens": "上下文长度", "model.license_name": "许可证",
}

UNIT_LABELS = {'gib': 'GiB', 'w': 'W', 'gb_s': 'GB/s', 'mhz': 'MHz', 'ghz': 'GHz'}


def catalogue_specs(session, entity):
    """Surface stored values without promoting catalogue presence to evidence."""
    rows = []
    models = [(GpuSpec, 'gpu'), (CpuSpec, 'cpu')] if entity.entity_type == 'hardware' else [(AIModel, 'model')] if entity.entity_type == 'ai_model' else []
    for model, prefix in models:
        row = session.get(model, entity.id)
        if row is None:
            continue
        for field, label in FIELD_LABELS.items():
            if not field.startswith(prefix + '.'):
                continue
            value = getattr(row, field.split('.', 1)[1], None)
            if value is None:
                continue
            unit = next((u for suffix, u in [('_gib', 'GiB'), ('_gb_s', 'GB/s'), ('_w', 'W'), ('_mhz', 'MHz'), ('_bit', 'bit'), ('_tokens', 'tokens')] if field.endswith(suffix)), '')
            rows.append({'field': field, 'label': label, 'value': str(value), 'unit': unit,
                         'status': 'catalogue_only', 'evidence_ids': []})
    return rows


def lookup_product(session, subject, token=None):
    # Parameterized LIKE; treat user wildcards literally, preserving word separators.
    words = subject.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_').split()
    pattern = '%' + '%'.join(words) + '%'
    entities = list(session.scalars(select(CatalogEntity).where(
        CatalogEntity.canonical_name.ilike(pattern, escape='\\'),
        CatalogEntity.entity_type.in_(['hardware', 'cpu', 'gpu', 'ai_model', 'model_variant']))
        .order_by(CatalogEntity.canonical_name).limit(8)))
    if token:
        entities = [e for e in entities if matches_sku(e.canonical_name, token)]
    exact = [e for e in entities if e.canonical_name.casefold() == subject.casefold()]
    if exact:
        entities = exact
    if len(entities) != 1:
        return entities, [], []
    entity = entities[0]
    evidence = session.scalars(select(EvidenceClaim).where(EvidenceClaim.entity_id == entity.id,
        EvidenceClaim.review_status == 'accepted', EvidenceClaim.field_key.in_([*FIELD_LABELS, 'entity.canonical_name']))
        .order_by(EvidenceClaim.field_key, EvidenceClaim.id).limit(48)).all()
    claims = []
    for row in evidence:
        value = row.normalized_value
        if value is None or isinstance(value, (dict, list)):
            continue
        claims.append({'claim_type': 'fact', 'entity_id': str(entity.id), 'field_key': row.field_key,
            'value': value, 'unit': row.unit_key, 'evidence_ids': [str(row.id)],
            'value_type': 'boolean' if isinstance(value, bool) else 'number' if isinstance(value, (int, float)) else 'string'})
    if not claims:
        return entities, [], []
    verified_claims = []
    verifier = TruthVerifier(SqlAlchemyTruthRepository(session))
    for start in range(0, len(claims), 6):
        request = Recommendation.model_validate({'recommendations': [{'candidate_id': str(entity.id),
            'candidate_type': 'hardware' if entity.entity_type in {'cpu', 'gpu'} else entity.entity_type,
            'name': entity.canonical_name, 'score': 0, 'reasons': ['Product fact lookup only'], 'claims': claims[start:start + 6]}]})
        verified_claims.extend(verifier.verify(request).candidates[0].claims)
    facts, ids, seen = [], set(), set()
    for claim in verified_claims:
        key = claim.claim.field_key
        if claim.status != 'supported' or not claim.valid_evidence_ids:
            continue
        ids.update(claim.valid_evidence_ids)
        if key in seen:
            continue
        seen.add(key)
        if key == 'entity.canonical_name':
            continue
        facts.append({'label': FIELD_LABELS[key], 'value': str(claim.canonical_value),
                      'unit': UNIT_LABELS.get(claim.canonical_unit, claim.canonical_unit) or '', 'field': key,
                      'status': 'verified', 'evidence_ids': [str(i) for i in claim.valid_evidence_ids]})
    sources = session.execute(select(SourceDocument.title, SourceDocument.url, EvidenceClaim.field_key)
        .join(EvidenceClaim, EvidenceClaim.source_id == SourceDocument.id)
        .where(EvidenceClaim.id.in_(ids)).distinct().order_by(SourceDocument.url)).all() if ids else []
    return entities, facts, [{'title': title, 'url': url, 'field': field,
                             'label': FIELD_LABELS.get(field, '产品名称')} for title, url, field in sources]


async def introduce_product(message, conversation_id, plan, *, anchor=None, event_sink=None):
    specs = [spec['required'] for spec in sku_requests(message).values() if spec['required']]
    subject = specs[0] if len(specs) == 1 else plan.subject.strip()
    refers_back = bool(re.search(r'这[张块个款]|刚才|上次|上一个', message))
    if refers_back and anchor and len(anchor.selected) == 1 and not specs:
        subject = anchor.selected[0].canonical_name
    with SessionLocal() as session:
        conversation = conversation_context_service.get_or_create_conversation(session, conversation_id)
        conversation_context_service.append_message(session, conversation, 'user', message)
        emit(event_sink, 'tool_started', 'tool', '正在查找产品资料', status='running', detail={'tool': 'product_lookup'})
        entities, facts, sources = lookup_product(session, subject, specs[0] if len(specs) == 1 else None) if subject else ([], [], [])
        emit(event_sink, 'tool_completed', 'tool', '产品资料查询完成', status='completed',
             detail={'tool': 'product_lookup', 'row_count': len(entities)})
        name = entities[0].canonical_name if len(entities) == 1 else subject
        if len(entities) == 1:
            known = {f['field'] for f in facts}
            facts.extend(f for f in catalogue_specs(session, entities[0]) if f['field'] not in known)
        if len(entities) > 1:
            answer = '找到多个对应型号：' + '、'.join(e.canonical_name for e in entities) + '。请确认要介绍的具体版本。'
        elif not entities:
            answer = f'暂未找到“{subject}”的可核对产品资料，请补充完整型号。' if subject else '请提供要介绍的产品型号。'
        elif not facts:
            answer = f'已找到 {name}，但当前没有足够的已核对规格资料可作介绍。'
        else:
            category = {'gpu': '显卡', 'cpu': '处理器', 'ai_model': '模型', 'model_variant': '模型版本', 'hardware': '硬件产品'}[entities[0].entity_type]
            if entities[0].entity_type == 'hardware':
                category = '显卡' if any(f['field'].startswith('gpu.') for f in facts) else '处理器' if any(f['field'].startswith('cpu.') for f in facts) else category
            answer = f'{name} 是一款{category}。以下为数据库已收录的资料。'
            emit(event_sink, 'verification', 'verification', '产品规格与来源已核对', status='completed')
        stored_answer = answer + '\n' + '\n'.join(f"{f['label']}：{f['value']} {f['unit']}（{'已核验' if f.get('status', 'verified') == 'verified' else '目录收录，待补证据'}）" for f in facts)
        conversation_context_service.append_message(session, conversation, 'assistant', stored_answer)
        session.commit()
        return {'kind': 'product_info', 'conversation_id': str(conversation.id), 'answer': answer,
                'model': '', 'context_compressed': False, 'product_name': name, 'sources': sources,
                'facts': facts, 'verification': ('partial' if any(f.get('status') == 'catalogue_only' for f in facts)
                                               else 'facts_verified' if facts else 'not_verified')}
