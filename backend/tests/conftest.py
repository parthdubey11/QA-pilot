"""Tests run against a real MongoDB (unique indexes and atomic job claims behave like production).

Start one with `docker compose up -d mongo`. Each test gets its own throwaway database.
Override the server with TEST_MONGO_URL (the api/worker containers set it to mongodb://mongo:27017).
"""

import os
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator

import chromadb
import httpx
import pytest
from chromadb.config import Settings as ChromaSettings
from pymongo.asynchronous.database import AsyncDatabase

from app.core import db
from app.core.ratelimit import login_limiter
from app.main import app
from app.rag import KnowledgeBase, set_knowledge_base
from app.rag.embeddings import HashingEmbeddings

TEST_MONGO_URL = os.getenv("TEST_MONGO_URL", "mongodb://localhost:27017")


_chroma = chromadb.EphemeralClient(ChromaSettings(anonymized_telemetry=False))


@pytest.fixture(autouse=True)
def knowledge() -> Iterator[KnowledgeBase]:
    """Every test gets its own in-memory vector store (hashing embeddings: no model download)."""
    name = f"test_{uuid.uuid4().hex[:12]}"
    kb = KnowledgeBase(_chroma, HashingEmbeddings(), name)
    set_knowledge_base(kb)
    yield kb
    set_knowledge_base(None)
    _chroma.delete_collection(name)


@pytest.fixture
async def database() -> AsyncIterator[AsyncDatabase]:
    name = f"qapilot_test_{uuid.uuid4().hex[:12]}"
    try:
        database = await db.init_db(TEST_MONGO_URL, name)
    except Exception as exc:  # noqa: BLE001
        await db.close_db()
        pytest.fail(f"MongoDB not reachable at {TEST_MONGO_URL} ({exc}). Run: docker compose up -d mongo")
    yield database
    await database.client.drop_database(name)
    await db.close_db()


@pytest.fixture
async def client(database: AsyncDatabase) -> AsyncIterator[httpx.AsyncClient]:
    # ASGITransport doesn't run the app lifespan, so the test database set up above is the one in use.
    login_limiter._failures.clear()  # the limiter is process-wide; start each test clean
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


RegisterFn = Callable[..., Awaitable[dict[str, str]]]


@pytest.fixture
def register(client: httpx.AsyncClient) -> RegisterFn:
    """Register a user and return Authorization headers for them."""

    async def _register(
        email: str = "alice@example.com", password: str = "correct-horse", name: str = "Alice"
    ) -> dict[str, str]:
        res = await client.post("/auth/register", json={"email": email, "password": password, "name": name})
        assert res.status_code == 201, res.text
        return {"Authorization": f"Bearer {res.json()['access_token']}"}

    return _register
