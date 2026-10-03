"""MongoDB connection. Beanie creates the collections and indexes declared on the models, so there are no migrations."""

import asyncio
import logging

from beanie import init_beanie
from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase

from app.core.config import get_settings
from app.models import DOCUMENT_MODELS

logger = logging.getLogger("qa_pilot.db")

_client: AsyncMongoClient | None = None


def make_client(url: str) -> AsyncMongoClient:
    # tz_aware: datetimes come back as timezone-aware UTC. Short server selection so failures surface quickly.
    return AsyncMongoClient(url, tz_aware=True, serverSelectionTimeoutMS=3000)


async def init_db(url: str | None = None, db_name: str | None = None) -> AsyncDatabase:
    """Connect and register the Beanie models (creates missing indexes)."""
    global _client
    settings = get_settings()
    _client = make_client(url or settings.mongo_url)
    database = _client[db_name or settings.mongo_db]
    await init_beanie(database=database, document_models=DOCUMENT_MODELS)
    return database


async def init_db_with_retry(poll_seconds: float = 3.0) -> AsyncDatabase:
    """Keep trying until MongoDB is reachable (used at API/worker startup)."""
    while True:
        try:
            return await init_db()
        except Exception as exc:  # noqa: BLE001 - any connection problem: log and retry
            logger.warning("MongoDB not reachable yet (%s), retrying in %.0fs", type(exc).__name__, poll_seconds)
            await close_db()
            await asyncio.sleep(poll_seconds)


async def close_db() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None


async def check_database() -> bool:
    """Return True if MongoDB answers a ping."""
    if _client is None:
        return False
    try:
        await _client.admin.command("ping")
        return True
    except Exception:  # noqa: BLE001
        return False
