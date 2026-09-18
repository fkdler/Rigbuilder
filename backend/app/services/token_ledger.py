"""Meter final provider responses once at the common HTTP boundary."""
import logging
from contextvars import ContextVar

from sqlalchemy.exc import SQLAlchemyError

from app.db.session import SessionLocal
from app.models.admin_records import TokenUsage

meter_site_usage: ContextVar[bool] = ContextVar("meter_site_usage", default=False)
logger = logging.getLogger(__name__)


def token_counts(response: object) -> dict:
    usage = response.get("usage", {}) if isinstance(response, dict) else {}
    usage = usage if isinstance(usage, dict) else {}
    def count(key):
        value = usage.get(key)
        return value if type(value) is int and 0 <= value <= 2**63 - 1 else None
    p, c, total = count("prompt_tokens"), count("completion_tokens"), count("total_tokens")
    if total is None and p is not None and c is not None and p + c <= 2**63 - 1:
        total = p + c
    return dict(prompt_tokens=p, completion_tokens=c, total_tokens=total)


def record_usage(response: object, model: str) -> None:
    if not meter_site_usage.get():
        return
    # Separate transaction; never depends on a job/user FK or on success parsing
    # the response into the recommendation protocol. Do not store prompts.
    try:
        with SessionLocal() as session:
            session.add(TokenUsage(model=model[:255], **token_counts(response)))
            session.commit()
    except SQLAlchemyError:
        # A telemetry outage must not turn a completed model call into a retry.
        logger.exception("token_usage_write_failed: site usage will be incomplete")
