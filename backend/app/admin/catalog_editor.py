"""Curated maintenance of recommendation facts, aliases and append-only quotes."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, StrictBool, StrictStr, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.db.session import get_db_session
from app.models import User
from app.models import truth_v3 as t
from app.models.admin_records import AdminChange

router = APIRouter(dependencies=[Depends(require_admin)])

# Deliberately hand-selected. Keys are mapped here, never interpreted as SQL.
# Only fields already registered as claimable columns in this deployment appear.
FIELDS = {
    "cpu.cores_total": (t.CpuSpec, "cores_total", "integer", "总核心数", "", 1, 4096),
    "cpu.threads": (t.CpuSpec, "threads", "integer", "线程数", "", 1, 8192),
    "cpu.socket": (t.CpuSpec, "socket", "text", "CPU 接口", "", 0, 80),
    "cpu.base_power_w": (t.CpuSpec, "base_power_w", "number", "基础功耗", "W", 0, 10000),
    "cpu.base_clock_mhz": (t.CpuSpec, "base_clock_mhz", "number", "基础频率", "MHz", 0, 20000),
    "cpu.boost_clock_mhz": (t.CpuSpec, "boost_clock_mhz", "number", "加速频率", "MHz", 0, 20000),
    "cpu.integrated_gpu": (t.CpuSpec, "integrated_gpu", "text", "核显型号（无核显填写 none）", "", 0, 160),
    "cpu.memory_channels": (t.CpuSpec, "memory_channels", "integer", "内存通道数", "", 1, 128),
    "gpu.vram_gib": (t.GpuSpec, "vram_gib", "number", "显存容量", "GiB", 0, 65536),
    "gpu.board_power_w": (t.GpuSpec, "board_power_w", "number", "显卡功耗", "W", 0, 10000),
    "gpu.memory_type": (t.GpuSpec, "memory_type", "text", "显存类型", "", 0, 80),
    "gpu.memory_bandwidth_gb_s": (t.GpuSpec, "memory_bandwidth_gb_s", "number", "显存带宽", "GB/s", 0, 1000000),
    "gpu.memory_bus_width_bit": (t.GpuSpec, "memory_bus_width_bit", "integer", "显存位宽", "bit", 1, 65536),
    "gpu.pcie_generation": (t.GpuSpec, "pcie_generation", "number", "PCIe 代际", "", 1, 10),
    "gpu.pcie_lanes": (t.GpuSpec, "pcie_lanes", "integer", "PCIe 通道数", "", 1, 128),
    "gpu.ecc_support": (t.GpuSpec, "ecc_support", "boolean", "ECC 支持", "", 0, 0),
    "model.total_parameters": (t.AIModel, "total_parameters", "integer", "总参数量（个，非 B）", "", 1, 10**15),
    "model.active_parameters": (t.AIModel, "active_parameters", "integer", "激活参数量（个，非 B）", "", 1, 10**15),
    "model.context_length_tokens": (t.AIModel, "context_length_tokens", "integer", "上下文长度", "Token", 1, 10**10),
    "model.license_name": (t.AIModel, "license_name", "text", "许可证名称", "", 0, 160),
}


def fail(code, message):
    raise HTTPException(code, detail=message)


def entity(session, entity_id, lock=False):
    query = select(t.CatalogEntity).where(t.CatalogEntity.id == entity_id,
        t.CatalogEntity.entity_type.in_(["hardware", "ai_model", "model_variant"]))
    row = session.scalar(query.with_for_update() if lock else query)
    if row is None:
        fail(404, "未找到硬件或模型。")
    return row


def scalar(value):
    if isinstance(value, (Decimal, datetime, UUID)):
        return str(value)
    return value


def editable(session, item):
    hardware = session.get(t.Hardware, item.id) if item.entity_type == "hardware" else None
    category = hardware.category if hardware else "model" if item.entity_type == "ai_model" else None
    definitions = {f.field_key: f for f in session.scalars(select(t.FieldDefinition).where(
        t.FieldDefinition.field_key.in_(FIELDS), t.FieldDefinition.active.is_(True), t.FieldDefinition.claimable.is_(True)))}
    result = {}
    for key, spec in FIELDS.items():
        if key.split(".")[0] != category:
            continue
        definition = definitions.get(key)
        model, column = spec[:2]
        if definition is None or definition.storage_kind != "column" or definition.storage_path != f"{model.__tablename__}.{column}":
            continue
        result[key] = (spec, definition, session.get(model, item.id))
    return result


def editor_state(session, item):
    fields = []
    for key, (spec, definition, row) in editable(session, item).items():
        _, column, kind, label, unit, minimum, maximum = spec
        value = getattr(row, column) if row is not None else None
        fields.append(dict(key=key, label=label, kind=kind, unit=unit, minimum=minimum, maximum=maximum,
                           value=scalar(value), missing=value is None))
    aliases = list(session.scalars(select(t.EntityAlias).where(t.EntityAlias.entity_id == item.id).order_by(t.EntityAlias.normalized_alias)))
    claims = session.execute(select(t.EvidenceClaim.id, t.EvidenceClaim.review_status).where(
        t.EvidenceClaim.entity_id == item.id, t.EvidenceClaim.field_key.in_(FIELDS)).order_by(t.EvidenceClaim.id)).all()
    prices = session.scalars(select(t.PriceSnapshot).where(t.PriceSnapshot.entity_id == item.id)
        .order_by(t.PriceSnapshot.observed_at.desc(), t.PriceSnapshot.id).limit(5)).all()
    changes = session.scalars(select(AdminChange).where(AdminChange.target_id == item.id)
        .order_by(AdminChange.created_at.desc(), AdminChange.id.desc()).limit(10)).all()
    fingerprint = dict(updated_at=str(item.updated_at), fields=fields,
        aliases=[(str(a.id), a.alias, a.normalized_alias) for a in aliases], claims=[(str(id), status) for id, status in claims],
        prices=[str(p.id) for p in prices])
    revision = sha256(json.dumps(fingerprint, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return dict(revision=revision, fields=fields, aliases=[a.alias for a in aliases],
        prices=[dict(amount=str(p.amount), currency=p.currency, market_region=p.market_region,
                     condition=p.condition, observed_at=p.observed_at, notes=p.notes) for p in prices],
        changes=[dict(id=c.id, action=c.action, created_at=c.created_at, before=c.before, after=c.after) for c in changes])


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Provenance(Strict):
    title: str = Field(min_length=2, max_length=255)
    url: HttpUrl
    excerpt: str = Field(min_length=2, max_length=4000)
    reason: str = Field(min_length=2, max_length=1000)
    confirmed: Literal[True]


class Maintenance(Strict):
    revision: str = Field(min_length=64, max_length=64)
    values: dict[str, StrictStr | StrictBool | None] = Field(default_factory=dict, max_length=20)
    aliases: list[str] | None = Field(default=None, max_length=100)
    provenance: Provenance | None = None

    @field_validator("aliases")
    @classmethod
    def aliases_valid(cls, values):
        if values is not None and any(not value.strip() or len(value) > 255 or any(ord(c) < 32 for c in value) for value in values):
            raise ValueError("别名必须为 1–255 个可见字符")
        return values


def parse_value(raw, spec):
    _, _, kind, label, _, minimum, maximum = spec
    if raw is None:
        return None
    if kind == "boolean":
        if type(raw) is not bool:
            fail(422, f"{label}请选择是、否或未知。")
        return raw
    if not isinstance(raw, str):
        fail(422, f"{label}的格式不正确。")
    raw = raw.strip()
    if not raw:
        fail(422, f"{label}留空请设为未知，不要提交空字符串。")
    if kind == "text":
        if len(raw) > maximum or any(ord(c) < 32 for c in raw):
            fail(422, f"{label}超出长度限制或含有控制字符。")
        return raw
    if len(raw) > 40 or not re.fullmatch(r"[0-9]+(?:\.[0-9]{1,8})?", raw):
        fail(422, f"{label}请填写非负数，不使用科学计数法或单位后缀。")
    try:
        value = Decimal(raw)
    except InvalidOperation:
        fail(422, f"{label}不是有效数值。")
    if not minimum <= value <= maximum or kind == "integer" and value != value.to_integral_value():
        fail(422, f"{label}应为 {minimum}–{maximum} 范围内的{'整数' if kind == 'integer' else '数值'}。")
    return int(value) if kind == "integer" else value


def source(session, provenance):
    now = datetime.now(timezone.utc)
    row = t.SourceDocument(id=uuid4(), source_key=f"admin-source-{uuid4()}", title=provenance.title,
        url=str(provenance.url), source_type="web_page", accessed_at=now,
        availability_status="unknown")
    session.add(row)
    session.flush()
    return row


def check_revision(session, item, revision):
    if editor_state(session, item)["revision"] != revision:
        fail(409, "数据已发生变化，请重新读取后核对修改。")


@router.get("/catalog/{entity_id}/maintenance")
def maintenance_get(entity_id: UUID, session: Session = Depends(get_db_session)):
    return editor_state(session, entity(session, entity_id))


@router.patch("/catalog/{entity_id}/maintenance")
def maintenance_save(entity_id: UUID, body: Maintenance, actor: User = Depends(require_admin),
                     session: Session = Depends(get_db_session)):
    item = entity(session, entity_id, lock=True)
    check_revision(session, item, body.revision)
    allowed = editable(session, item)
    if set(body.values) - allowed.keys():
        fail(422, "提交了当前实体不允许维护的字段。")
    parsed = {key: parse_value(raw, allowed[key][0]) for key, raw in body.values.items()}
    before = {key: scalar(getattr(allowed[key][2], allowed[key][0][1], None)) for key in parsed}
    parsed = {key: val for key, val in parsed.items() if scalar(val) != before[key]}
    if parsed and body.provenance is None:
        fail(422, "规格修改需要填写来源、摘录和修改原因，并确认已核对。")
    # Validate merged values, including unchanged columns in the same row.
    def merged(key):
        if key in parsed:
            return parsed[key]
        spec, _, row = allowed.get(key, (None, None, None))
        return getattr(row, spec[1], None) if row is not None else None
    for low, high, message in [
        ("cpu.cores_total", "cpu.threads", "线程数不能小于核心数。"),
        ("cpu.base_clock_mhz", "cpu.boost_clock_mhz", "加速频率不能小于基础频率。"),
        ("model.active_parameters", "model.total_parameters", "激活参数量不能大于总参数量。"),
    ]:
        if not {low, high}.intersection(parsed):
            continue
        lo, hi = merged(low), merged(high)
        if lo is not None and hi is not None and lo > hi:
            fail(422, message)
    old_aliases = session.scalars(select(t.EntityAlias).where(t.EntityAlias.entity_id == item.id)).all()
    new_aliases = None
    if body.aliases is not None:
        new_aliases = {a.strip().casefold(): a.strip() for a in body.aliases}
        if len(new_aliases) != len(body.aliases):
            fail(422, "别名不能重复（忽略大小写）。")
        collisions = session.scalars(select(t.EntityAlias.alias).where(
            t.EntityAlias.entity_id != item.id, t.EntityAlias.normalized_alias.in_(new_aliases))).all()
        if collisions:
            fail(409, "该别名已属于其他实体，请使用更明确的名称。")
        names = session.scalars(select(t.CatalogEntity.canonical_name).where(t.CatalogEntity.id != item.id,
            t.CatalogEntity.canonical_name.in_(new_aliases.values()))).all()
        if names:
            fail(409, "别名与其他实体的正式名称重名，请使用更明确的名称。")
    now = datetime.now(timezone.utc)
    audit_before, audit_after = {}, {}
    if parsed:
        evidence_source = source(session, body.provenance)
        previous_by_field = {key: [] for key in parsed}
        for claim in session.scalars(select(t.EvidenceClaim).where(t.EvidenceClaim.entity_id == item.id,
            t.EvidenceClaim.field_key.in_(parsed), t.EvidenceClaim.review_status == "accepted")):
            previous_by_field[claim.field_key].append(claim)
        row_cache = {}
        for key, value in parsed.items():
            spec, definition, row = allowed[key]
            model, column = spec[:2]
            if row is None:
                row = row_cache.get(model)
                if row is None:
                    pk = "entity_id" if model is t.AIModel else "hardware_id"
                    row = model(**{pk: item.id})
                    session.add(row)
                    row_cache[model] = row
            setattr(row, column, value)
            audit_before[key] = before[key]
            audit_after[key] = scalar(value)
            # Keep old evidence content intact. Withdraw its accepted status so
            # verifiers cannot use an old assertion to justify the new fact.
            previous = previous_by_field[key]
            for claim in previous:
                claim.review_status = "conflict"
            audit_before[f"{key}:accepted_evidence"] = [str(c.id) for c in previous]
            if value is not None:
                claim = t.EvidenceClaim(id=uuid4(), entity_id=item.id, source_id=evidence_source.id,
                    field_key=key, normalized_value=float(value) if isinstance(value, Decimal) else value,
                    unit_key=definition.canonical_unit, review_status="accepted", confidence=1,
                    raw_excerpt=body.provenance.excerpt, source_locator=str(body.provenance.url),
                    provenance_key="admin_review", collected_at=now, reviewed_at=now,
                    notes=body.provenance.reason)
                session.add(claim)
                audit_after[f"{key}:evidence_id"] = str(claim.id)
        audit_after["source"] = body.provenance.model_dump(mode="json")
    if new_aliases is not None:
        existing = {a.normalized_alias: a for a in old_aliases}
        if {a.normalized_alias: a.alias for a in old_aliases} != new_aliases:
            audit_before["aliases"] = sorted(a.alias for a in old_aliases)
            audit_after["aliases"] = sorted(new_aliases.values())
            for key, row in existing.items():
                if key not in new_aliases:
                    session.delete(row)
            for key, name in new_aliases.items():
                if key in existing:
                    existing[key].alias = name
                else:
                    session.add(t.EntityAlias(id=uuid4(), entity_id=item.id, alias=name, normalized_alias=key, alias_type="alternate"))
    if audit_after:
        item.updated_at = now
        session.add(AdminChange(actor_id=actor.id, target_id=item.id, action="maintain_catalog", before=audit_before, after=audit_after))
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        fail(409, "保存与现有数据约束冲突，请重新读取后检查。")
    return editor_state(session, item)


class Quote(Strict):
    revision: str = Field(min_length=64, max_length=64)
    amount: Decimal = Field(gt=0, le=1000000000, max_digits=14, decimal_places=4, allow_inf_nan=False)
    currency: Literal["CNY", "USD"]
    market_region: Literal["CN", "US", "GLOBAL"]
    condition: Literal["new", "used"] = "new"
    observed_at: datetime
    provenance: Provenance

    @field_validator("observed_at")
    @classmethod
    def observed(cls, value):
        if value.tzinfo is None or value > datetime.now(timezone.utc) + timedelta(minutes=5) or value.year < 2000:
            raise ValueError("报价时间需包含时区，且不能是未来日期")
        return value


@router.post("/catalog/{entity_id}/quotes", status_code=201)
def add_quote(entity_id: UUID, body: Quote, actor: User = Depends(require_admin), session: Session = Depends(get_db_session)):
    item = entity(session, entity_id, lock=True)
    check_revision(session, item, body.revision)
    if item.entity_type != "hardware":
        fail(422, "此处只维护硬件购买价格，模型 API 计费与权重下载不能作为硬件报价。")
    origin = source(session, body.provenance)
    row = t.PriceSnapshot(id=uuid4(), price_key=f"admin-price-{uuid4()}", entity_id=item.id, source_id=origin.id,
        amount=body.amount, currency=body.currency, market_region=body.market_region,
        condition=body.condition, observed_at=body.observed_at, price_type="retail", notes=body.provenance.reason)
    session.add(row)
    item.updated_at = datetime.now(timezone.utc)
    session.add(AdminChange(actor_id=actor.id, target_id=item.id, action="append_quote", before={},
        after={"price_id": str(row.id), **body.model_dump(mode="json", exclude={"revision"})}))
    session.commit()
    return editor_state(session, item)
