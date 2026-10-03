from datetime import timedelta

import httpx
import pytest

from app.core.security import create_token
from app.models.user import User
from tests.conftest import RegisterFn

ALICE = {"email": "alice@example.com", "password": "correct-horse", "name": "Alice"}


async def test_register_returns_user_and_tokens(client: httpx.AsyncClient) -> None:
    res = await client.post("/auth/register", json=ALICE | {"email": "Alice@Example.com"})

    assert res.status_code == 201
    body = res.json()
    assert body["user"]["email"] == "alice@example.com"
    assert len(body["user"]["id"]) == 24  # ObjectId as a string
    assert body["token_type"] == "bearer"
    assert body["access_token"] and body["refresh_token"]
    assert "password" not in str(body["user"])


async def test_password_is_stored_as_bcrypt_hash(register: RegisterFn) -> None:
    await register(password="correct-horse")

    user = await User.find_one(User.email == "alice@example.com")
    assert user is not None
    assert user.password_hash.startswith("$2") and "correct-horse" not in user.password_hash


async def test_register_rejects_duplicate_email_case_insensitively(
    client: httpx.AsyncClient, register: RegisterFn
) -> None:
    await register(email="alice@example.com")

    res = await client.post("/auth/register", json=ALICE | {"email": "ALICE@example.com"})

    assert res.status_code == 409
    assert await User.count() == 1


async def test_unique_email_index_blocks_racing_duplicates(client: httpx.AsyncClient) -> None:
    import asyncio

    results = await asyncio.gather(*(client.post("/auth/register", json=ALICE) for _ in range(5)))

    assert sorted(r.status_code for r in results) == [201, 409, 409, 409, 409]
    assert await User.count() == 1


async def test_register_validates_input(client: httpx.AsyncClient) -> None:
    bodies = [
        ALICE | {"password": "short"},
        ALICE | {"email": "not-an-email"},
        ALICE | {"password": "x" * 73},  # longer than bcrypt's 72-byte limit
        ALICE | {"name": "  "},
    ]
    codes = [(await client.post("/auth/register", json=b)).status_code for b in bodies]

    assert codes == [422, 422, 422, 422]


async def test_login_with_correct_password(client: httpx.AsyncClient, register: RegisterFn) -> None:
    await register()

    res = await client.post("/auth/login", json={"email": "Alice@example.com", "password": "correct-horse"})

    assert res.status_code == 200
    assert res.json()["user"]["email"] == "alice@example.com"


async def test_login_rejects_wrong_password_and_unknown_email(client: httpx.AsyncClient, register: RegisterFn) -> None:
    await register()

    wrong = await client.post("/auth/login", json={"email": "alice@example.com", "password": "wrong-horse"})
    unknown = await client.post("/auth/login", json={"email": "bob@example.com", "password": "correct-horse"})

    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json() == {"detail": "Invalid email or password."}


async def test_me_requires_valid_access_token(client: httpx.AsyncClient, register: RegisterFn) -> None:
    headers = await register()

    assert (await client.get("/auth/me", headers=headers)).json()["email"] == "alice@example.com"
    assert (await client.get("/auth/me")).status_code == 401
    assert (await client.get("/auth/me", headers={"Authorization": "Bearer garbage"})).status_code == 401


async def test_expired_access_token_is_rejected(client: httpx.AsyncClient, register: RegisterFn) -> None:
    await register()
    user = await User.find_one()
    assert user is not None
    expired = create_token(str(user.id), "access", ttl=timedelta(seconds=-1))

    assert (await client.get("/auth/me", headers={"Authorization": f"Bearer {expired}"})).status_code == 401


async def test_token_with_non_objectid_subject_is_rejected(client: httpx.AsyncClient, database) -> None:
    token = create_token("not-an-object-id", "access")

    assert (await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})).status_code == 401


async def test_refresh_token_issues_new_access_token(client: httpx.AsyncClient) -> None:
    tokens = (await client.post("/auth/register", json=ALICE)).json()

    res = await client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})

    assert res.status_code == 200
    new_access = res.json()["access_token"]
    assert (await client.get("/auth/me", headers={"Authorization": f"Bearer {new_access}"})).status_code == 200


async def test_tokens_cannot_be_used_for_the_wrong_purpose(client: httpx.AsyncClient) -> None:
    tokens = (await client.post("/auth/register", json=ALICE)).json()

    refresh_as_access = await client.get("/auth/me", headers={"Authorization": f"Bearer {tokens['refresh_token']}"})
    access_as_refresh = await client.post("/auth/refresh", json={"refresh_token": tokens["access_token"]})

    assert refresh_as_access.status_code == 401
    assert access_as_refresh.status_code == 401


async def test_token_for_deleted_user_is_rejected(client: httpx.AsyncClient, register: RegisterFn) -> None:
    headers = await register()
    user = await User.find_one()
    assert user is not None
    await user.delete()

    assert (await client.get("/auth/me", headers=headers)).status_code == 401


async def test_login_locks_out_after_repeated_failures(client: httpx.AsyncClient, register: RegisterFn) -> None:
    await register(email="carol@example.com", password="correct-horse")
    for _ in range(10):
        r = await client.post("/auth/login", json={"email": "carol@example.com", "password": "wrong-password"})
        assert r.status_code == 401
    locked = await client.post("/auth/login", json={"email": "Carol@example.com", "password": "correct-horse"})
    assert locked.status_code == 429 and int(locked.headers["Retry-After"]) > 0
    assert "Too many failed attempts" in locked.json()["detail"]
    other = await client.post("/auth/login", json={"email": "dave@example.com", "password": "whatever1"})
    assert other.status_code == 401  # other accounts are unaffected


async def test_invite_code_required_when_configured(client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "registration_code", "letmein")
    body = {"email": "erin@example.com", "password": "correct-horse", "name": "Erin"}
    assert (await client.post("/auth/register", json=body)).status_code == 403
    assert (await client.post("/auth/register", json=body | {"invite_code": "nope"})).status_code == 403
    assert (await client.post("/auth/register", json=body | {"invite_code": " letmein "})).status_code == 201
