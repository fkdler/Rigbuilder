"""Real SQL on an isolated SQLite store; never touches the configured database."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import JSON, MetaData, create_engine, event, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.main import app
from app.models import AuthSession, Conversation, QueryEvent, QueryJob, User
from app.models.admin_records import AdminChange, TokenUsage
from app.models.truth_v3 import AIModel, CatalogEntity, CpuSpec, Hardware
from app.services.token_ledger import meter_site_usage, record_usage, token_counts
from auth_helpers import authenticate, session_override

client = TestClient(app)


@pytest.fixture()
def store():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def configure(connection, _):
        connection.execute("ATTACH DATABASE ':memory:' AS truth")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.create_function("num_nonnulls", -1, lambda *args: sum(v is not None for v in args))

    metadata = MetaData()
    for table in Base.metadata.tables.values():
        copy = table.to_metadata(metadata)
        for column in copy.columns:
            if isinstance(column.type, JSONB):
                column.type = JSON()
    metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        session_override(session)
        authenticate()
        yield session
    engine.dispose()


@pytest.mark.parametrize("path", ["usage", "users", "catalog", f"catalog/{uuid4()}"])
def test_read_routes_require_admin(store, path):
    authenticate(role="user")
    assert client.get(f"/api/admin/{path}").status_code == 403


def test_writes_require_admin(store):
    authenticate(role="user")
    assert client.request("DELETE", f"/api/admin/users/{uuid4()}", json={"username": "x"}).status_code == 403


def test_users_search_and_delete_cascade_preserves_usage(store):
    user = User(username="Alice_%", password_hash="not-exposed", role="user")
    admin = User(username="Admin", password_hash="not-exposed", role="admin")
    store.add_all([user, admin]); store.flush()
    conversation = Conversation(owner_id=user.id)
    store.add(conversation); store.flush()
    job = QueryJob(owner_id=user.id, conversation_id=conversation.id, requested_mode="auto", status="completed", request_payload={})
    store.add(job); store.flush()
    store.add_all([QueryEvent(job_id=job.id, sequence=1, event_type="llm_completed", phase="llm", title="done"),
                   TokenUsage(model="m", total_tokens=42)])
    store.commit()
    response = client.get("/api/admin/users", params={"q": "_%"})
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert "password" not in response.text
    assert client.request("DELETE", f"/api/admin/users/{admin.id}", json={"username": "Admin"}).status_code == 409
    assert client.request("DELETE", f"/api/admin/users/{user.id}", json={"username": "wrong"}).status_code == 409
    assert client.request("DELETE", f"/api/admin/users/{user.id}", json={"username": user.username}).status_code == 204
    store.expire_all()
    assert store.scalar(select(func.count()).select_from(QueryJob)) == 0
    assert store.scalar(select(func.count()).select_from(Conversation)) == 0
    assert store.scalar(select(func.count()).select_from(QueryEvent)) == 0
    assert store.scalar(select(func.sum(TokenUsage.total_tokens))) == 42
    assert store.scalar(select(AdminChange.action)) == "delete_user"


def test_running_job_blocks_deletion(store):
    user = User(username="Busy", password_hash="x", role="user")
    store.add(user); store.flush()
    store.add(QueryJob(owner_id=user.id, requested_mode="auto", status="running", request_payload={}))
    store.commit()
    assert client.request("DELETE", f"/api/admin/users/{user.id}", json={"username": "Busy"}).status_code == 409


def test_usage_cst_boundary_unknown_and_empty_days(store):
    store.add_all([
        TokenUsage(model="m", total_tokens=10, created_at=datetime(2026, 9, 15, 15, 59, tzinfo=timezone.utc)),
        TokenUsage(model="m", total_tokens=20, created_at=datetime(2026, 9, 15, 16, 0, tzinfo=timezone.utc)),
        TokenUsage(model="m", total_tokens=None, created_at=datetime(2026, 9, 16, 1, tzinfo=timezone.utc)),
    ]); store.commit()
    result = client.get("/api/admin/usage?start=2026-09-16&end=2026-09-17")
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["total_tokens"] == 30 and data["period_tokens"] == 20
    assert data["unknown_calls"] == 1
    assert data["by_day"] == [dict(day="2026-09-16", total_tokens=20, calls=2, unknown_calls=1),
                              dict(day="2026-09-17", total_tokens=0, calls=0, unknown_calls=0)]
    assert client.get("/api/admin/usage?start=2026-09-17&end=2026-09-16").status_code == 422
    assert client.get("/api/admin/usage?start=2020-01-01&end=2026-09-16").status_code == 422


def test_request_timing_success_only_weighted_and_cst_boundaries(store):
    boundary = datetime(2026, 9, 15, 16, tzinfo=timezone.utc)

    def job(created, wait, execution, status="completed", missing=False):
        return QueryJob(requested_mode="auto", request_payload={}, status=status, created_at=created,
                        started_at=None if missing else created + timedelta(seconds=wait),
                        completed_at=created + timedelta(seconds=wait + execution))

    store.add_all([
        job(boundary - timedelta(seconds=1), 1, 999),
        job(boundary, 2, 8), job(boundary + timedelta(hours=1), 4, 16),
        job(boundary + timedelta(days=1), 0, 60),
        job(boundary, 1, 900, status="failed"), job(boundary, 1, 900, status="cancelled"),
        job(boundary, 1, 900, status="running"), job(boundary, 1, 900, missing=True),
        job(boundary, -1, 900), job(boundary, 1, -1),
        job(boundary + timedelta(days=3), 1, 999),
    ])
    store.commit()
    result = client.get("/api/admin/usage?start=2026-09-16&end=2026-09-18")
    assert result.status_code == 200, result.text
    timing = result.json()["timing"]
    assert timing["samples"] == 3
    assert timing["avg_response_seconds"] == 30
    assert timing["avg_execution_seconds"] == 28
    assert timing["avg_start_wait_seconds"] == 2
    assert timing["by_day"][0]["avg_response_seconds"] == 15
    assert timing["by_day"][1]["avg_response_seconds"] == 60
    assert timing["by_day"][2] == dict(day="2026-09-18", samples=0,
        avg_response_seconds=None, avg_execution_seconds=None, avg_start_wait_seconds=None)
    empty = client.get("/api/admin/usage?start=2026-09-18&end=2026-09-18").json()["timing"]
    assert empty["samples"] == 0 and empty["avg_response_seconds"] is None


def test_catalog_details_edit_conflict_and_audit(store):
    entity = CatalogEntity(id=uuid4(), entity_key="hardware/test", entity_type="hardware", canonical_name="CPU Test",
                           lifecycle_status="active", recommendable=True)
    model = CatalogEntity(id=uuid4(), entity_key="model/test", entity_type="ai_model", canonical_name="Model Test")
    store.add_all([entity, model]); store.flush()
    store.add_all([Hardware(entity_id=entity.id, category="cpu"), AIModel(entity_id=model.id, total_parameters=32000000000)])
    store.flush(); store.add(CpuSpec(hardware_id=entity.id, cores_total=8)); store.commit()
    assert client.get("/api/admin/catalog?kind=model").json()["total"] == 1
    detail = client.get(f"/api/admin/catalog/{entity.id}")
    assert detail.status_code == 200, detail.text
    data = detail.json()
    assert {s["table"] for s in data["sections"]} == {"hardware", "cpu_spec"}
    patch = dict(updated_at=data["entity"]["updated_at"], canonical_name="Updated CPU", lifecycle_status="legacy",
                 recommendable=False, release_date="2024-01-02")
    response = client.patch(f"/api/admin/catalog/{entity.id}", json=patch)
    assert response.status_code == 200, response.text
    assert response.json()["canonical_name"] == "Updated CPU"
    assert store.scalar(select(AdminChange.action)) == "update_catalog"
    assert client.patch(f"/api/admin/catalog/{entity.id}", json=patch).status_code == 409
    assert client.patch(f"/api/admin/catalog/{entity.id}", json={**patch, "entity_key": "illegal"}).status_code == 422
    assert client.patch(f"/api/admin/catalog/{entity.id}", json={**patch, "canonical_name": "  "}).status_code == 422
    authenticate(role="user")
    assert client.patch(f"/api/admin/catalog/{entity.id}", json=patch).status_code == 403


def test_provider_counts_are_not_estimated():
    assert token_counts({"usage": {"prompt_tokens": 10, "completion_tokens": 5}})["total_tokens"] == 15
    assert token_counts({"usage": {"prompt_tokens": True, "completion_tokens": -1, "total_tokens": "50"}})["total_tokens"] is None
    assert token_counts(None)["total_tokens"] is None


def test_ledger_only_meters_site_calls(store, monkeypatch):
    from contextlib import contextmanager
    @contextmanager
    def factory():
        yield store
    monkeypatch.setattr("app.services.token_ledger.SessionLocal", factory)
    record_usage({"usage": {"total_tokens": 8}}, "probe")
    assert store.scalar(select(func.count()).select_from(TokenUsage)) == 0
    token = meter_site_usage.set(True)
    try:
        record_usage({"usage": {"total_tokens": 8}}, "site")
        record_usage(None, "failed")
    finally:
        meter_site_usage.reset(token)
    assert store.scalar(select(func.count()).select_from(TokenUsage)) == 2
    assert store.scalar(select(func.sum(TokenUsage.total_tokens))) == 8


def maintenance_fixture(store, *, kind="hardware", with_spec=True):
    from app.models.truth_v3 import FieldDefinition
    item = CatalogEntity(id=uuid4(), entity_key=f"{kind}/{uuid4()}", entity_type=kind,
                         canonical_name="Editable", lifecycle_status="active", recommendable=True)
    store.add(item); store.flush()
    if kind == "hardware":
        store.add(Hardware(entity_id=item.id, category="cpu")); store.flush()
        if with_spec:
            store.add(CpuSpec(hardware_id=item.id, cores_total=8, threads=16))
        definitions = [("cpu.cores_total", "cpu_spec.cores_total"), ("cpu.threads", "cpu_spec.threads")]
    else:
        store.add(AIModel(entity_id=item.id, total_parameters=32000000000, active_parameters=4000000000))
        definitions = [("model.total_parameters", "ai_model.total_parameters"), ("model.active_parameters", "ai_model.active_parameters")]
    for key, path in definitions:
        store.add(FieldDefinition(id=uuid4(), field_key=key, label=key, value_type="integer",
                  applies_to_entity_type=kind, storage_kind="column", storage_path=path,
                  active=True, claimable=True))
    store.commit()
    return item


def provenance():
    return dict(title="Official specification", url="https://example.com/spec", excerpt="12 cores, 24 threads",
                reason="Correct vendor specification", confirmed=True)


def maintenance_path(item):
    return f"/api/admin/catalog/{item.id}/maintenance"


def test_maintenance_missing_spec_created_with_evidence_and_audit(store):
    from app.models.truth_v3 import EvidenceClaim, SourceDocument
    item = maintenance_fixture(store, with_spec=False)
    state = client.get(maintenance_path(item)).json()
    assert len(state["fields"]) == 2 and all(f["missing"] for f in state["fields"])
    body = dict(revision=state["revision"], values={"cpu.cores_total": "12", "cpu.threads": "24"}, provenance=provenance())
    response = client.patch(maintenance_path(item), json=body)
    assert response.status_code == 200, response.text
    row = store.get(CpuSpec, item.id)
    assert (row.cores_total, row.threads) == (12, 24)
    claims = store.scalars(select(EvidenceClaim)).all()
    assert len(claims) == 2 and all(c.review_status == "accepted" for c in claims)
    assert store.scalar(select(func.count()).select_from(SourceDocument)) == 1
    assert store.scalar(select(AdminChange.action)) == "maintain_catalog"
    assert client.patch(maintenance_path(item), json=body).status_code == 409
    assert store.scalar(select(func.count()).select_from(EvidenceClaim)) == 2


def test_maintenance_withdraws_old_evidence_and_clear_stays_unknown(store):
    from app.models.truth_v3 import EvidenceClaim, SourceDocument
    item = maintenance_fixture(store)
    origin = SourceDocument(id=uuid4(), source_key="old", title="Old source", url="https://example.com/old",
                            source_type="web_page", accessed_at=datetime.now(timezone.utc))
    store.add(origin); store.flush()
    old = EvidenceClaim(id=uuid4(), source_id=origin.id, entity_id=item.id, field_key="cpu.cores_total",
                        normalized_value=8, review_status="accepted", raw_excerpt="8 cores")
    store.add(old); store.commit()
    revision = client.get(maintenance_path(item)).json()["revision"]
    response = client.patch(maintenance_path(item), json=dict(revision=revision,
        values={"cpu.cores_total": None}, provenance=provenance()))
    assert response.status_code == 200, response.text
    assert store.get(CpuSpec, item.id).cores_total is None
    store.refresh(old)
    assert old.review_status == "conflict" and old.normalized_value == 8 and old.raw_excerpt == "8 cores"
    assert store.scalar(select(func.count()).select_from(EvidenceClaim)) == 1


@pytest.mark.parametrize("values", [
    {"cpu.threads": "4"}, {"cpu.cores_total": "3.5"}, {"cpu.cores_total": "NaN"},
    {"cpu.cores_total": "10000000000000000000000"}, {"cpu.cores_total": "-1"},
    {"cpu.cores_total": True}, {"gpu.vram_gib": "16"}, {"entity_key": "wrong"},
])
def test_maintenance_rejects_invalid_or_unrelated_values_atomically(store, values):
    from app.models.truth_v3 import SourceDocument
    item = maintenance_fixture(store)
    revision = client.get(maintenance_path(item)).json()["revision"]
    response = client.patch(maintenance_path(item), json=dict(revision=revision, values=values,
                                                            aliases=["Do not save"], provenance=provenance()))
    assert response.status_code == 422, response.text
    assert store.get(CpuSpec, item.id).cores_total == 8
    assert store.scalar(select(func.count()).select_from(AdminChange)) == 0
    assert store.scalar(select(func.count()).select_from(SourceDocument)) == 0


def test_maintenance_requires_source_and_detects_external_spec_update(store):
    item = maintenance_fixture(store)
    revision = client.get(maintenance_path(item)).json()["revision"]
    assert client.patch(maintenance_path(item), json=dict(revision=revision, values={"cpu.threads": "24"})).status_code == 422
    row = store.get(CpuSpec, item.id); row.threads = 32; store.commit()
    assert client.patch(maintenance_path(item), json=dict(revision=revision,
        values={"cpu.threads": "24"}, provenance=provenance())).status_code == 409


def test_model_parameters_validate_merged_state(store):
    item = maintenance_fixture(store, kind="ai_model")
    revision = client.get(maintenance_path(item)).json()["revision"]
    response = client.patch(maintenance_path(item), json=dict(revision=revision,
        values={"model.total_parameters": "3000000000"}, provenance=provenance()))
    assert response.status_code == 422
    response = client.patch(maintenance_path(item), json=dict(revision=revision,
        values={"model.total_parameters": "3000000000", "model.active_parameters": "2000000000"}, provenance=provenance()))
    assert response.status_code == 200, response.text


def test_aliases_save_without_spec_source_and_preserve_existing_metadata(store):
    from app.models.truth_v3 import EntityAlias
    item = maintenance_fixture(store)
    old = EntityAlias(id=uuid4(), entity_id=item.id, alias="Old", normalized_alias="old", alias_type="marketing", language="en")
    store.add(old); store.commit()
    revision = client.get(maintenance_path(item)).json()["revision"]
    assert client.patch(maintenance_path(item), json=dict(revision=revision, aliases=["Old", "OLD"])).status_code == 422
    response = client.patch(maintenance_path(item), json=dict(revision=revision, aliases=["Old", "新别名"]))
    assert response.status_code == 200, response.text
    store.refresh(old)
    assert old.alias_type == "marketing" and old.language == "en"
    assert set(response.json()["aliases"]) == {"Old", "新别名"}


def test_quotes_append_preserve_history_and_reject_retry(store):
    from app.models.truth_v3 import PriceSnapshot
    item = maintenance_fixture(store)
    revision = client.get(maintenance_path(item)).json()["revision"]
    body = dict(revision=revision, amount="1999.50", currency="CNY", market_region="CN", condition="new",
                observed_at="2026-09-01T12:00:00+08:00", provenance=provenance())
    response = client.post(f"/api/admin/catalog/{item.id}/quotes", json=body)
    assert response.status_code == 201, response.text
    assert client.post(f"/api/admin/catalog/{item.id}/quotes", json=body).status_code == 409
    body.update(revision=response.json()["revision"], amount="1899", observed_at="2026-09-02T12:00:00+08:00")
    assert client.post(f"/api/admin/catalog/{item.id}/quotes", json=body).status_code == 201
    prices = store.scalars(select(PriceSnapshot).order_by(PriceSnapshot.observed_at)).all()
    assert [str(p.amount) for p in prices] == ["1999.5000", "1899.0000"]


def test_maintenance_permissions_and_quote_validation(store):
    item = maintenance_fixture(store)
    revision = client.get(maintenance_path(item)).json()["revision"]
    body = dict(revision=revision, amount="0", currency="CNY", market_region="CN",
                observed_at="2099-09-01T12:00:00+08:00", provenance=provenance())
    assert client.post(f"/api/admin/catalog/{item.id}/quotes", json=body).status_code == 422
    authenticate(role="user")
    assert client.get(maintenance_path(item)).status_code == 403
    assert client.patch(maintenance_path(item), json=dict(revision=revision, aliases=[])).status_code == 403
    assert client.post(f"/api/admin/catalog/{item.id}/quotes", json={**body, "amount": "100", "observed_at": "2026-09-01T12:00:00+08:00"}).status_code == 403
