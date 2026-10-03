import httpx
import pytest

from app.core import db


async def test_health_ok_when_database_reachable(client: httpx.AsyncClient) -> None:
    res = await client.get("/health")

    assert res.status_code == 200
    assert res.json() == {"status": "ok", "database": "ok", "version": "0.1.0",
                          "registration_code_required": False, "max_tests_limit": 15}


async def test_health_degraded_when_database_down(client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def down() -> bool:
        return False

    monkeypatch.setattr(db, "check_database", down)

    res = await client.get("/health")

    assert res.status_code == 200
    assert res.json()["status"] == "degraded"
    assert res.json()["database"] == "unavailable"


async def test_check_database_false_before_connecting() -> None:
    assert await db.check_database() is False
