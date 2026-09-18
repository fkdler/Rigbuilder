"""Bounded local-model discovery and evidence-qualified deployment advice."""
import re
from decimal import Decimal, InvalidOperation

from sqlalchemy import select

from app.models.truth_v3 import EntityAttribute, FieldDefinition, EvidenceClaim, SourceDocument, ModelVariant
from app.schemas.recommendation import Recommendation
from app.verification import TruthVerifier
from app.verification.repository import SqlAlchemyTruthRepository


def quantization(message):
    matches = list(re.finditer(r'(?<![a-z0-9])(Q[234568](?:_K_[SML]|_0)?|AWQ|GPTQ)(?![a-z0-9_])', message, re.I))
    value = matches[-1].group().upper() if matches else 'Q4_K_M'
    return {'Q4': 'Q4_K_M', 'Q8': 'Q8_0'}.get(value, value)


def memory_limit(message):
    for kind in ('显存', '(?<!显)内存'):
        matches = list(re.finditer(rf'(\d+(?:\.\d+)?)\s*(?:GB|GiB|G)\s*{kind}|{kind}\s*(\d+(?:\.\d+)?)\s*(?:GB|GiB|G)', message, re.I))
        if matches:
            return float(next(g for g in matches[-1].groups() if g))
    return None


def discovery_queries(message):
    # Interpolation is restricted to numbers, enums and regex-limited model tokens.
    qualifier = quantization(message)
    limit = memory_limit(message)
    filters = ["m.recommendable = true", "m.model_stage IN ('instruct', 'chat')",
               "m.model_type IN ('dense', 'moe')", "m.total_parameters IS NOT NULL"]
    from app.services.model_choices import excluded_model_ids, canonical_ids
    excluded = canonical_ids(excluded_model_ids.get())
    if excluded:
        filters.append("m.entity_id NOT IN (" + ','.join("'" + value + "'" for value in excluded) + ")")
    if limit:
        # Loading estimates are only an initial exclusion bound, never a fit guarantee.
        filters.append("EXISTS (SELECT 1 FROM agent_catalog.entity_attribute_catalog a WHERE a.entity_id=m.entity_id "
                       f"AND a.field_key='model.deployment_estimated_load_gb' AND a.qualifier_key='{qualifier}' "
                       f"AND a.value_status='known' AND a.value_number > 0 AND a.value_number < {limit})")
    elif not re.search(r'(?<![a-z0-9])(qwen|deepseek|llama|gemma|phi)[a-z0-9.\-]*', message, re.I):
        filters.append('m.total_parameters BETWEEN 1000000000 AND 4500000000' if re.search(r'更小|轻量|小一点|smaller', message, re.I)
                       else 'm.total_parameters BETWEEN 3000000000 AND 9000000000')
    if re.search(r'量化|quant|GGUF|Q[234568]', message, re.I):
        filters.append("EXISTS (SELECT 1 FROM agent_catalog.model_variant_catalog v WHERE v.model_key=m.entity_key "
                       f"AND v.quantization_method='{qualifier}' AND v.repository_url IS NOT NULL)")
    # Task-specific support must be explicit catalogue metadata, not a family-name guess.
    capability = 'code' if re.search(r'写代码|编程|coding|code', message, re.I) else 'reasoning' if re.search(r'推理能力|数学|reasoning', message, re.I) else None
    if capability:
        filters.append("EXISTS (SELECT 1 FROM agent_catalog.entity_attribute_catalog a WHERE a.entity_id=m.entity_id "
                       f"AND a.field_key='model.capability.{capability}' AND a.value_text='supported')")
    # A named model/family stays a filter; never silently replace it with another family.
    family = re.search(r'(?<![a-z0-9])(qwen|deepseek|llama|gemma|phi)([a-z0-9.\-]*)', message, re.I)
    if family:
        filters.append("m.name ILIKE '%" + family.group().replace('-', '%') + "%'")
    if not family and not limit:
        # Known quantization conditions provide a more useful starting point than
        # family familiarity. This preference is catalogue coverage, not quality.
        order = "CASE WHEN EXISTS (SELECT 1 FROM agent_catalog.entity_attribute_catalog a WHERE a.entity_id=m.entity_id "
        order += f"AND a.field_key='model.deployment_estimated_load_gb' AND a.qualifier_key='{qualifier}' AND a.value_status='known') THEN 0 ELSE 1 END,"
    else:
        order = ''
    if re.search(r'更小|轻量|小一点|smaller', message, re.I):
        filters.append('m.total_parameters <= 4500000000')
    return ["SELECT m.entity_id,m.entity_key,m.name,m.total_parameters,m.context_length_tokens,m.license_name "
            "FROM agent_catalog.model_catalog m WHERE " + ' AND '.join(filters)
            + " ORDER BY " + order + "m.total_parameters DESC,m.name LIMIT 3"]


def enrich_model_presentation(session, narration, result, message):
    """Add actual quantization conditions, preserving evidence and non-measurement labels."""
    if not result.top_k or result.top_k[0].candidate_type != 'ai_model' or narration.primary is None:
        return
    candidate = result.top_k[0]
    qualifier = quantization(message)
    rows = session.execute(select(EntityAttribute, FieldDefinition).join(FieldDefinition, FieldDefinition.id == EntityAttribute.field_id)
        .where(EntityAttribute.entity_id == candidate.candidate_id, EntityAttribute.qualifier_key == qualifier,
               EntityAttribute.value_status == 'known', FieldDefinition.field_key.in_(
                   ['model.deployment_estimated_load_gb', 'model.deployment_min_ram_gb']))).all()
    statements, sources = [], []
    for attr, field in rows:
        value = attr.value_number
        if value is None:
            continue
        evidence = session.scalars(select(EvidenceClaim).where(EvidenceClaim.entity_id == candidate.candidate_id,
            EvidenceClaim.field_key == field.field_key, EvidenceClaim.review_status == 'accepted')).all()
        claims = [{'claim_type': 'fact', 'entity_id': str(candidate.candidate_id), 'field_key': field.field_key,
            'qualifier_key': qualifier, 'value': float(value), 'value_type': 'number', 'unit': attr.unit,
            'evidence_ids': [str(e.id)]} for e in evidence]
        if not claims:
            continue
        checked = TruthVerifier(SqlAlchemyTruthRepository(session)).verify(Recommendation.model_validate({'recommendations': [{
            'candidate_id': str(candidate.candidate_id), 'candidate_type': 'ai_model', 'name': candidate.canonical_name,
            'score': 0, 'reasons': ['Read the selected quantization condition'], 'claims': claims}]})).candidates[0]
        ids = {i for c in checked.claims if c.status == 'supported' for i in c.valid_evidence_ids}
        if not ids:
            # Some imported deployment fields declare range comparison but store a
            # scalar estimate. Surface matching source records as estimates only;
            # never change the verifier or promote these to supported claims.
            for e in evidence:
                try:
                    if e.unit_key == attr.unit and Decimal(str(e.normalized_value)) == value:
                        ids.add(e.id)
                except (InvalidOperation, ValueError, TypeError):
                    continue
        if not ids:
            continue
        label = '加载占用估计' if field.field_key.endswith('load_gb') else '系统内存参考值'
        statements.append(f'{qualifier}：{label} {format(value.normalize(), "f")} GiB')
        for title, url in session.execute(select(SourceDocument.title, SourceDocument.url).join(EvidenceClaim,
            EvidenceClaim.source_id == SourceDocument.id).where(EvidenceClaim.id.in_(ids)).distinct()):
            sources.append({'title': title, 'url': url, 'field': field.field_key})
    variants = session.scalars(select(ModelVariant).where(ModelVariant.model_id == candidate.candidate_id,
        ModelVariant.quantization_method == qualifier, ModelVariant.repository_url.is_not(None))
        .order_by(ModelVariant.official_status, ModelVariant.entity_id).limit(2)).all()
    reasons = [f'可以先考虑 {candidate.canonical_name} 的 {qualifier} 版本。']
    if statements:
        reasons.append('部署时为上下文缓存和运行时预留内存，具体参考值见下方。')
        narration.evidence.extend(statements)
    if variants:
        reasons.append(f'可从下方仓库查看 {variants[0].weight_format} 文件，并选择推理框架支持的版本。')
        sources.extend({'title': f'{candidate.canonical_name} {qualifier} 下载目录', 'url': v.repository_url,
                        'field': 'download_catalogue_reference'} for v in variants)
    missing = []
    if not re.search(r'RTX|GTX|\bRX\s*\d|核显|CPU\s*运行|纯CPU', message, re.I): missing.append('显卡型号')
    if not re.search(r'显存', message): missing.append('显存')
    if not re.search(r'(?<!显)内存|RAM', message, re.I): missing.append('系统内存')
    if not re.search(r'聊天|对话|写代码|编程|看图|OCR|数学|文生图', message, re.I): missing.append('用途（聊天、编程或看图）')
    if missing:
        reasons.append('补充' + '、'.join(missing) + '后，可以进一步判断部署余量。')
    narration.primary = narration.primary.model_copy(update={'reasons': reasons[:4]})
    narration.sources = [*narration.sources, *sources]
