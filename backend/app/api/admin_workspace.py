"""Admin-only usage, account lifecycle and a deliberately bounded catalogue editor."""
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, extract, func, select
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.db.session import get_db_session
from app.models import AuthSession, QueryJob, User
from app.models.admin_records import AdminChange, TokenUsage
from app.models import truth_v3 as truth

router = APIRouter(dependencies=[Depends(require_admin)])
CST = timezone(timedelta(hours=8))


def request_timing(session: Session, lower: datetime, upper: datetime, days: list[str]):
    """Successful persisted jobs, bucketed by submission date; aggregate in SQL."""
    sqlite = session.bind.dialect.name == "sqlite"

    def seconds(later, earlier):
        return ((func.julianday(later) - func.julianday(earlier)) * 86400
                if sqlite else extract("epoch", later - earlier))

    day = (func.date(QueryJob.created_at, "+8 hours") if sqlite
           else func.date(func.timezone("Asia/Shanghai", QueryJob.created_at)))
    valid = (QueryJob.created_at >= lower, QueryJob.created_at < upper,
             QueryJob.status == "completed", QueryJob.started_at.is_not(None),
             QueryJob.completed_at.is_not(None), QueryJob.started_at >= QueryJob.created_at,
             QueryJob.completed_at >= QueryJob.started_at)
    metrics = (func.count(), func.avg(seconds(QueryJob.completed_at, QueryJob.created_at)),
               func.avg(seconds(QueryJob.completed_at, QueryJob.started_at)),
               func.avg(seconds(QueryJob.started_at, QueryJob.created_at)))

    def values(row):
        return dict(samples=row[0], avg_response_seconds=round(float(row[1]), 3) if row[1] is not None else None,
                    avg_execution_seconds=round(float(row[2]), 3) if row[2] is not None else None,
                    avg_start_wait_seconds=round(float(row[3]), 3) if row[3] is not None else None)

    summary = values(session.execute(select(*metrics).where(*valid)).one())
    rows = session.execute(select(day, *metrics).where(*valid).group_by(day)).all()
    daily = {str(row[0]): values(row[1:]) for row in rows}
    return {**summary, "by_day": [dict(day=d, **daily.get(d, values((0, None, None, None)))) for d in days],
            "note": "按请求提交日期统计现存、时间完整且顺序有效的成功咨询任务。响应耗时为提交至完整结果完成，执行耗时为开始执行至完成，启动等待为提交至开始执行（不含执行中的模型排队）。不代表首字延迟；失败、取消、未完成和已删除的任务不计入。"}


def error(status: int, message: str):
    raise HTTPException(status_code=status, detail=message)


@router.get("/usage")
def usage(start: date | None = None, end: date | None = None,
          session: Session = Depends(get_db_session)):
    end = end or datetime.now(CST).date()
    if not date(1970, 2, 1) <= end <= date(9998, 12, 31):
        error(422, "结束日期超出支持范围。")
    start = start or end - timedelta(days=29)
    if not 0 <= (end - start).days <= 365:
        error(422, "请选择不超过 366 天且起止顺序正确的日期范围。")
    lower = datetime.combine(start, time.min, CST).astimezone(timezone.utc)
    upper = datetime.combine(end + timedelta(days=1), time.min, CST).astimezone(timezone.utc)
    within = (TokenUsage.created_at >= lower, TokenUsage.created_at < upper)
    aggregate = (func.coalesce(func.sum(TokenUsage.total_tokens), 0), func.count(),
                 func.count(TokenUsage.total_tokens))
    total, calls, known = session.execute(select(*aggregate).select_from(TokenUsage)).one()
    period_total, period_calls, period_known = session.execute(select(*aggregate).where(*within)).one()
    day = (func.date(TokenUsage.created_at, "+8 hours") if session.bind.dialect.name == "sqlite"
           else func.date(func.timezone("Asia/Shanghai", TokenUsage.created_at)))
    rows = session.execute(select(day.label("day"), *aggregate).where(*within).group_by(day)).all()
    by_day = {str(row[0]): {"day": str(row[0]), "total_tokens": row[1], "calls": row[2],
                           "unknown_calls": row[2] - row[3]} for row in rows}
    days = [str(start + timedelta(days=i)) for i in range((end - start).days + 1)]
    first = session.scalar(select(func.min(TokenUsage.created_at)))
    live_since = session.scalar(select(func.min(TokenUsage.created_at)).where(TokenUsage.source == "provider"))
    return dict(total_tokens=total, calls=calls, unknown_calls=calls-known,
                period_tokens=period_total, period_calls=period_calls, period_unknown_calls=period_calls-period_known,
                first_record_at=first, metering_since=live_since, timezone="Asia/Shanghai",
                timing=request_timing(session, lower, upper, days),
                start=start, end=end, by_day=[by_day.get(d, dict(day=d, total_tokens=0, calls=0, unknown_calls=0)) for d in days],
                note="历史数据仅含已保存的调用记录；启用新计量后覆盖网站各推理环节。未返回用量、流中断或记录失败的消耗无法还原，数值为已记录用量。")


def user_row(user: User):
    return {key: getattr(user, key) for key in ("id", "username", "role", "status", "created_at", "last_login_at")}


@router.get("/users")
def users(q: str = Query("", max_length=100), offset: int = Query(0, ge=0),
          limit: int = Query(25, ge=1, le=100), session: Session = Depends(get_db_session)):
    condition = User.username.icontains(q.strip(), autoescape=True)
    count = session.scalar(select(func.count()).select_from(User).where(condition))
    rows = session.scalars(select(User).where(condition).order_by(User.created_at.desc(), User.id).offset(offset).limit(limit))
    return dict(total=count, items=[user_row(row) for row in rows])


class DeleteAccount(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str


@router.delete("/users/{user_id}", status_code=204)
def delete_user(user_id: UUID, payload: DeleteAccount, actor: User = Depends(require_admin),
                session: Session = Depends(get_db_session)):
    user = session.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None:
        error(404, "账号不存在或已被删除。")
    if user.id == actor.id or user.role == "admin":
        error(409, "不能删除管理员账号。")
    if payload.username != user.username:
        error(409, "用户名不匹配，请刷新后重新确认。")
    active = session.scalar(select(QueryJob.id).where(QueryJob.owner_id == user_id,
        QueryJob.status.in_(["queued", "running", "cancel_requested"])).limit(1))
    if active:
        error(409, "该用户还有运行中的任务，请等待任务结束后再删除。")
    session.add(AdminChange(actor_id=actor.id, target_id=user.id, action="delete_user",
                            before={"role": user.role}, after={"deleted": True}))
    session.execute(delete(AuthSession).where(AuthSession.user_id == user_id))
    # Migration 0010 owns the cascading deletion of conversations and jobs.
    session.delete(user)
    session.commit()
    return Response(status_code=204)


ENTITY_TYPES = ("hardware", "ai_model", "model_variant")


def entity_row(entity):
    return {key: getattr(entity, key) for key in ("id", "entity_key", "entity_type", "canonical_name",
            "lifecycle_status", "recommendable", "release_date", "updated_at")}


@router.get("/catalog")
def catalog(kind: Literal["all", "hardware", "model"] = "all", q: str = Query("", max_length=100),
            offset: int = Query(0, ge=0), limit: int = Query(25, ge=1, le=100),
            session: Session = Depends(get_db_session)):
    types = ENTITY_TYPES if kind == "all" else (("hardware",) if kind == "hardware" else ("ai_model", "model_variant"))
    conditions = (truth.CatalogEntity.entity_type.in_(types),
                  (truth.CatalogEntity.canonical_name.icontains(q.strip(), autoescape=True) |
                   truth.CatalogEntity.entity_key.icontains(q.strip(), autoescape=True)))
    count = session.scalar(select(func.count()).select_from(truth.CatalogEntity).where(*conditions))
    rows = session.scalars(select(truth.CatalogEntity).where(*conditions)
                           .order_by(truth.CatalogEntity.canonical_name, truth.CatalogEntity.id).offset(offset).limit(limit))
    return dict(total=count, items=[entity_row(row) for row in rows])


def load_entity(session, entity_id, *, lock=False):
    query = select(truth.CatalogEntity).where(truth.CatalogEntity.id == entity_id,
                                             truth.CatalogEntity.entity_type.in_(ENTITY_TYPES))
    entity = session.scalar(query.with_for_update() if lock else query)
    if entity is None:
        error(404, "未找到硬件或模型记录。")
    return entity


def serialize_record(row):
    # Decimal/large integers are sent as text to avoid losing precision in JS.
    from decimal import Decimal
    return {col.name: str(value) if isinstance(value, Decimal) or type(value) is int and abs(value) > 2**53-1 else jsonable_encoder(value)
            for col in row.__table__.columns if (value := getattr(row, col.name)) is not None}


@router.get("/catalog/{entity_id}")
def catalog_detail(entity_id: UUID, session: Session = Depends(get_db_session)):
    entity = load_entity(session, entity_id)
    sections = []
    # Explicit, trusted tables; never accept a table name or SQL from the client.
    tables = [(truth.Hardware, "entity_id"), (truth.AIModel, "entity_id"), (truth.ModelVariant, "entity_id"),
              (truth.CpuSpec, "hardware_id"), (truth.GpuSpec, "hardware_id"),
              (truth.LaptopGpuSpec, "hardware_id"), (truth.DatacenterGpuSpec, "hardware_id"),
              (truth.MemorySpec, "hardware_id"), (truth.StorageSpec, "hardware_id"),
              (truth.PsuSpec, "hardware_id"), (truth.PlatformSpec, "hardware_id"),
              (truth.EntityAlias, "entity_id"), (truth.EntityAttribute, "entity_id"),
              (truth.ModelCapability, "model_id"), (truth.PriceSnapshot, "entity_id")]
    for model, key in tables:
        column = getattr(model, key)
        count = session.scalar(select(func.count()).select_from(model).where(column == entity_id))
        if not count:
            continue
        rows = session.scalars(select(model).where(column == entity_id).order_by(*model.__table__.primary_key.columns).limit(100))
        sections.append(dict(table=model.__tablename__, total=count, rows=[serialize_record(row) for row in rows]))
    return dict(entity=entity_row(entity), sections=sections)


class EntityPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    updated_at: datetime
    canonical_name: str = Field(min_length=1, max_length=255)
    lifecycle_status: Literal["announced", "upcoming", "active", "legacy", "discontinued", "unknown"]
    recommendable: bool = Field(strict=True)
    release_date: date | None = None

    @field_validator("canonical_name")
    @classmethod
    def clean_name(cls, value):
        value = value.strip()
        if not value or any(ord(c) < 32 for c in value):
            raise ValueError("名称不能为空或包含控制字符")
        return value


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


@router.patch("/catalog/{entity_id}")
def update_entity(entity_id: UUID, payload: EntityPatch, actor: User = Depends(require_admin),
                  session: Session = Depends(get_db_session)):
    entity = load_entity(session, entity_id, lock=True)
    if utc(entity.updated_at) != utc(payload.updated_at):
        error(409, "记录已被其他操作修改，请重新打开详情后再编辑。")
    before = jsonable_encoder(entity_row(entity))
    for key, value in payload.model_dump(exclude={"updated_at"}).items():
        setattr(entity, key, value)
    entity.updated_at = datetime.now(timezone.utc)
    after = jsonable_encoder(entity_row(entity))
    session.add(AdminChange(actor_id=actor.id, target_id=entity.id, action="update_catalog", before=before, after=after))
    session.commit()
    return entity_row(entity)
