from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


engine = create_engine(get_settings().database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, class_=Session, autoflush=False, expire_on_commit=False)


def get_db_session():
    """FastAPI dependency for a transactional database session when APIs are added."""
    with SessionLocal() as session:
        yield session


@lru_cache
def get_readonly_engine() -> Engine | None:
    """Build the agent_readonly engine, or return None when it is not configured.

    The connection options enforce the physical backstop from Plan V2.2 §3.5:
    statement_timeout and a read-only transaction default. query_database() must
    fail closed when this returns None rather than reuse the privileged engine.
    """
    settings = get_settings()
    url = settings.agent_readonly_database_url
    if not url:
        return None
    return create_engine(
        url,
        pool_pre_ping=True,
        # V3.3: three Agents run query_database() concurrently inside one fusion
        # request. Default SQLAlchemy pools are too small for 3 * sql budget.
        pool_size=settings.readonly_pool_size,
        max_overflow=settings.readonly_max_overflow,
        connect_args={
            "options": (
                f"-c statement_timeout={settings.sql_statement_timeout_ms} "
                "-c default_transaction_read_only=on "
                "-c search_path=agent_catalog,public"
            )
        },
    )
