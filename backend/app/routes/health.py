from fastapi import APIRouter

from app.core import db
from app.core.config import get_settings
from app.schemas.health import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Public liveness check; reports whether MongoDB is reachable."""
    db_ok = await db.check_database()
    return HealthResponse(
        status="ok" if db_ok else "degraded",
        database="ok" if db_ok else "unavailable",
        version=get_settings().app_version,
        registration_code_required=bool(get_settings().registration_code),
        max_tests_limit=get_settings().max_tests_limit,
    )
