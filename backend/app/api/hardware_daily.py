"""Signed-in reader endpoint for the separate hardware-daily surface."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.core.config import get_settings
from app.db.session import get_db_session
from app.schemas.hardware_daily import HardwareDailyResponse
from app.services.hardware_daily import HardwareDailyService, HardwareDailyUnavailableError

router = APIRouter(
    prefix="/api/hardware-daily",
    tags=["hardware daily"],
    dependencies=[Depends(current_user)],
)


@router.get("/latest", response_model=HardwareDailyResponse)
async def latest_hardware_daily(
    session: Session = Depends(get_db_session),
) -> HardwareDailyResponse:
    try:
        return await HardwareDailyService(get_settings()).latest(session)
    except HardwareDailyUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "hardware_daily_unavailable", "message": str(exc)},
        ) from exc
