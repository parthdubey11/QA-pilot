import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import get_settings
from app.core.db import close_db, init_db_with_retry
from app.routes import accessibility, auth, bugs, health, overview, projects, runs, saved_tests

settings = get_settings()
logger = logging.getLogger("qa_pilot")
if settings.jwt_secret == "change-me":
    logger.warning("JWT_SECRET is the insecure default; set it in .env")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    await init_db_with_retry()
    yield
    await close_db()


app = FastAPI(title="QA Pilot API", version=settings.app_version, lifespan=lifespan)
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(projects.router)
app.include_router(runs.router)
app.include_router(bugs.router)
app.include_router(accessibility.router)
app.include_router(saved_tests.router)
app.include_router(overview.router)
