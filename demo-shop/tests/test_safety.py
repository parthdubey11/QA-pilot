"""Public-demo safeguards: they must work without fixing any planted bug."""

from collections.abc import Iterator
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import safety
from app.main import app
from app.store import state


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        assert c.post("/reset").status_code == 200
        safety.limiter.hits.clear()
        yield c


def test_banner_noindex_and_robots(client: TestClient) -> None:
    for path in ("/", "/login", "/cart", "/products/1"):
        res = client.get(path)
        assert "Intentionally buggy demo site for testing QA Pilot. Don't enter real information." in res.text
        assert '<meta name="robots" content="noindex, nofollow">' in res.text
        assert res.headers["x-robots-tag"] == "noindex, nofollow"
    login_page = client.get("/login").text
    assert "<title>" not in login_page  # planted A08 (missing title) is untouched
    assert '<header class="site-header">\n  <p class="demo-banner" role="note">' in login_page  # inside the landmark
    assert client.get("/robots.txt").text == "User-agent: *\nDisallow: /\n"
    assert client.get("/docs").status_code == 404


def test_reset_needs_token_when_configured(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RESET_TOKEN", "s3cret")
    assert client.post("/reset").status_code == 403
    assert client.post("/reset", headers={"X-Reset-Token": "wrong"}).status_code == 403
    assert client.post("/admin/ui-variant", json={"variant": "v2"}).status_code == 403
    assert client.post("/reset", headers={"X-Reset-Token": "s3cret"}).status_code == 200


def test_rate_limit_per_client(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(safety.limiter, "per_minute", 5)
    codes = [client.get("/api/products").status_code for _ in range(7)]
    assert codes == [200] * 5 + [429, 429]
    monkeypatch.setenv("TRUST_PROXY", "1")
    assert client.get("/api/products", headers={"X-Forwarded-For": "203.0.113.9"}).status_code == 200  # another client


def test_caps_keep_planted_cart_bugs(client: TestClient) -> None:
    assert client.post("/api/cart/items", json={"product_id": 1, "quantity": -2}).status_code == 200  # F01 still there
    assert client.post("/api/cart/items", json={"product_id": 2, "quantity": 50}).status_code == 200  # above stock (F14)
    assert client.post("/api/cart/items", json={"product_id": 2, "quantity": 5000}).status_code == 400
    assert client.patch("/api/cart/items/2", json={"quantity": -1000}).status_code == 400


def test_signup_cap_and_field_lengths(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    long = "x" * 600
    assert client.post("/api/signup", json={"name": long, "email": "a@b.co", "password": "pw"}).status_code == 400
    monkeypatch.setattr(safety, "MAX_USERS", len(state.users))
    res = client.post("/api/signup", json={"name": "Full", "email": "full@b.co", "password": "password1"})
    assert res.status_code == 503 and "resets every night" in res.json()["detail"]


def test_trim_oldest_and_nightly_schedule() -> None:
    d = {i: i for i in range(5)}
    safety.trim_oldest(d, 3)
    assert list(d) == [3, 4]
    at = datetime(2026, 10, 1, 2, 30, tzinfo=timezone.utc)
    assert safety.seconds_until(3, at) == 30 * 60
    assert safety.seconds_until(3, datetime(2026, 10, 1, 3, 0, tzinfo=timezone.utc)) == 24 * 3600
