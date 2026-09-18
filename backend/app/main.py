from contextlib import asynccontextmanager
import time

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.admin import router as admin_router
from app.api.auth import router as auth_router
from app.api.chat import router as chat_router
from app.api.conversations import router as conversations_router
from app.api.hardware_daily import router as hardware_daily_router
from app.api.query import router as query_router
from app.core.config import get_settings
from app.core.responses import UTF8JSONResponse
from app.db.session import engine

from app.services.jobs import query_job_manager


@asynccontextmanager
async def lifespan(_app: FastAPI):
    query_job_manager.recover_after_restart()
    settings = get_settings()
    from app.inference.context_window import refresh_context_windows
    await refresh_context_windows(settings, force=True)
    from app.inference.profiles import parallel_configuration_issues
    profile_issues = parallel_configuration_issues(settings)
    if profile_issues:
        message = "; ".join(profile_issues)
        if settings.agent_parallel_required:
            raise RuntimeError(f"Invalid required parallel Agent configuration: {message}")
        print(f"[capability] serial fallback: {message}")
    if settings.capability_smoke_on_startup:
        try:
            from app.agents.capabilities import startup_capability_smoke
            results = await startup_capability_smoke(settings)
            summary = ", ".join(f"{profile_id}={state}" for profile_id, (state, _) in results.items())
            print(f"[capability] startup smoke: {summary or 'no profiles configured'}")
            if settings.agent_parallel_required and any(state != "ready" for state, _ in results.values()):
                raise RuntimeError(f"Required parallel Agent capability smoke failed: {summary}")
        except Exception as exc:  # never block startup on an optional probe
            if settings.agent_parallel_required:
                raise
            print(f"[capability] startup smoke skipped: {exc}")
    yield


settings = get_settings()
app = FastAPI(title=settings.app_name, version="0.4.0", lifespan=lifespan,
              default_response_class=UTF8JSONResponse)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.frontend_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(conversations_router)
app.include_router(hardware_daily_router)
app.include_router(query_router)
app.include_router(admin_router)


@app.get("/health", tags=["infrastructure"])
def health() -> dict[str, str]:
    """Process health only; it does not require PostgreSQL to be running."""
    return {"status": "ok"}


@app.get("/health/database", tags=["infrastructure"])
def database_health() -> dict[str, object]:
    """PostgreSQL connectivity plus which Truth DB release is actually loaded.

    The original ``status`` and ``database`` fields keep their exact meaning because
    readiness checks and operational probes read them; everything else is additive.
    A missing release row or an unreadable version table degrades that field to ``null``
    instead of failing the probe, so the readiness check stays a readiness check.
    """
    started = time.perf_counter()
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            release_key: str | None = None
            release_status: str | None = None
            try:
                row = connection.execute(text(
                    "SELECT release_key, status FROM truth.dataset_release "
                    "ORDER BY created_at DESC LIMIT 1"
                )).one_or_none()
                if row is not None:
                    release_key, release_status = str(row[0]), str(row[1])
            except SQLAlchemyError:
                pass
            views: int | None = None
            tables: int | None = None
            version: str | None = None
            try:
                views = int(connection.execute(text(
                    "SELECT count(*) FROM information_schema.views "
                    "WHERE table_schema = 'agent_catalog'"
                )).scalar() or 0)
                tables = int(connection.execute(text(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_schema = 'truth' AND table_type = 'BASE TABLE'"
                )).scalar() or 0)
            except SQLAlchemyError:
                pass
            try:
                raw = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
                version = str(raw) if raw else None
            except SQLAlchemyError:
                pass
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Database connection failed") from exc

    return {
        "status": "ok",
        "database": "reachable",
        "release_key": release_key,
        "release_status": release_status,
        "alembic_version": version,
        "agent_view_count": views,
        "truth_table_count": tables,
        "latency_ms": int((time.perf_counter() - started) * 1000),
    }
