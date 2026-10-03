import httpx
import pytest
from bson import ObjectId

from app.core.security import decrypt_secret
from app.models.project import Credential
from tests.conftest import RegisterFn

NEW_PROJECT = {"name": "Demo Shop", "base_url": "http://localhost:8080", "authorised_testing_confirmed": True}
CREDENTIAL = {"label": "Test shopper", "username": "demo@shop.test", "password": "demo1234"}
SOME_ID = str(ObjectId())


async def create_project(client: httpx.AsyncClient, headers: dict[str, str], **overrides: object) -> dict:
    res = await client.post("/projects", json=NEW_PROJECT | overrides, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


async def add_credential(client: httpx.AsyncClient, headers: dict[str, str], project_id: str, **overrides: object) -> dict:
    res = await client.post(f"/projects/{project_id}/credentials", json=CREDENTIAL | overrides, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


# ---------- auth required ----------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/projects"),
        ("post", "/projects"),
        ("get", f"/projects/{SOME_ID}"),
        ("patch", f"/projects/{SOME_ID}"),
        ("delete", f"/projects/{SOME_ID}"),
        ("get", f"/projects/{SOME_ID}/credentials"),
        ("post", f"/projects/{SOME_ID}/credentials"),
        ("patch", f"/projects/{SOME_ID}/credentials/{SOME_ID}"),
        ("delete", f"/projects/{SOME_ID}/credentials/{SOME_ID}"),
    ],
)
async def test_project_routes_require_auth(client: httpx.AsyncClient, method: str, path: str) -> None:
    assert (await client.request(method, path)).status_code == 401


# ---------- CRUD ----------


async def test_create_and_list_projects(client: httpx.AsyncClient, register: RegisterFn) -> None:
    headers = await register()
    project = await create_project(client, headers)

    assert project["name"] == "Demo Shop"
    assert project["base_url"].startswith("http://localhost:8080")
    assert project["is_own_site"] is False and project["security_probes_enabled"] is False
    assert "owner_id" not in project
    assert [p["id"] for p in (await client.get("/projects", headers=headers)).json()] == [project["id"]]


async def test_projects_listed_newest_first(client: httpx.AsyncClient, register: RegisterFn) -> None:
    headers = await register()
    for name in ("first", "second", "third"):
        await create_project(client, headers, name=name)

    names = [p["name"] for p in (await client.get("/projects", headers=headers)).json()]

    assert names == ["third", "second", "first"]


async def test_create_requires_authorised_testing_confirmation(client: httpx.AsyncClient, register: RegisterFn) -> None:
    headers = await register()

    unticked = await client.post("/projects", json=NEW_PROJECT | {"authorised_testing_confirmed": False}, headers=headers)
    missing = await client.post("/projects", json={"name": "X", "base_url": "http://x.test"}, headers=headers)

    assert unticked.status_code == missing.status_code == 422
    assert (await client.get("/projects", headers=headers)).json() == []


@pytest.mark.parametrize("url", ["not a url", "ftp://example.com", "javascript:alert(1)", ""])
async def test_create_rejects_non_http_urls(client: httpx.AsyncClient, register: RegisterFn, url: str) -> None:
    res = await client.post("/projects", json=NEW_PROJECT | {"base_url": url}, headers=await register())
    assert res.status_code == 422


async def test_update_project(client: httpx.AsyncClient, register: RegisterFn) -> None:
    headers = await register()
    project = await create_project(client, headers)

    res = await client.patch(
        f"/projects/{project['id']}", json={"name": "Shop v2", "base_url": "https://shop.example.com"}, headers=headers
    )

    assert res.status_code == 200
    assert res.json()["name"] == "Shop v2"
    assert res.json()["base_url"].startswith("https://shop.example.com")
    assert res.json()["updated_at"] >= project["updated_at"]


async def test_security_probes_need_own_site(client: httpx.AsyncClient, register: RegisterFn) -> None:
    headers = await register()
    pid = (await create_project(client, headers))["id"]

    refused = await client.patch(f"/projects/{pid}", json={"security_probes_enabled": True}, headers=headers)
    assert refused.status_code == 422

    allowed = await client.patch(
        f"/projects/{pid}", json={"is_own_site": True, "security_probes_enabled": True}, headers=headers
    )
    assert allowed.json()["security_probes_enabled"] is True

    unmarked = await client.patch(f"/projects/{pid}", json={"is_own_site": False}, headers=headers)
    assert unmarked.json()["security_probes_enabled"] is False  # turning off "own site" disables probes


async def test_delete_project_also_deletes_credentials(client: httpx.AsyncClient, register: RegisterFn) -> None:
    headers = await register()
    pid = (await create_project(client, headers))["id"]
    await add_credential(client, headers, pid)

    assert (await client.delete(f"/projects/{pid}", headers=headers)).status_code == 204
    assert (await client.get(f"/projects/{pid}", headers=headers)).status_code == 404
    assert await Credential.count() == 0


@pytest.mark.parametrize("bad_id", ["123", "not-an-id", "zzzzzzzzzzzzzzzzzzzzzzzz"])
async def test_malformed_ids_are_404(client: httpx.AsyncClient, register: RegisterFn, bad_id: str) -> None:
    headers = await register()

    assert (await client.get(f"/projects/{bad_id}", headers=headers)).status_code == 404


# ---------- owner isolation ----------


async def test_users_only_see_their_own_projects(client: httpx.AsyncClient, register: RegisterFn) -> None:
    alice = await register(email="alice@example.com")
    bob = await register(email="bob@example.com")
    alice_project = await create_project(client, alice, name="Alice's shop")
    await create_project(client, bob, name="Bob's shop")

    assert [p["name"] for p in (await client.get("/projects", headers=alice)).json()] == ["Alice's shop"]
    assert [p["name"] for p in (await client.get("/projects", headers=bob)).json()] == ["Bob's shop"]
    assert (await client.get(f"/projects/{alice_project['id']}", headers=alice)).status_code == 200


async def test_other_users_project_looks_like_it_does_not_exist(client: httpx.AsyncClient, register: RegisterFn) -> None:
    alice = await register(email="alice@example.com")
    bob = await register(email="bob@example.com")
    pid = (await create_project(client, alice))["id"]
    cid = (await add_credential(client, alice, pid))["id"]

    attempts = [
        await client.get(f"/projects/{pid}", headers=bob),
        await client.patch(f"/projects/{pid}", json={"name": "hacked"}, headers=bob),
        await client.delete(f"/projects/{pid}", headers=bob),
        await client.get(f"/projects/{pid}/credentials", headers=bob),
        await client.post(f"/projects/{pid}/credentials", json=CREDENTIAL, headers=bob),
        await client.patch(f"/projects/{pid}/credentials/{cid}", json={"password": "x"}, headers=bob),
        await client.delete(f"/projects/{pid}/credentials/{cid}", headers=bob),
    ]

    assert [r.status_code for r in attempts] == [404] * len(attempts)
    missing = await client.get(f"/projects/{SOME_ID}", headers=bob)
    assert attempts[0].json() == missing.json()  # same response as a project that doesn't exist
    project = (await client.get(f"/projects/{pid}", headers=alice)).json()
    assert project["name"] == "Demo Shop"
    assert len((await client.get(f"/projects/{pid}/credentials", headers=alice)).json()) == 1
    stored = await Credential.find_one()
    assert stored is not None and decrypt_secret(stored.password_encrypted) == "demo1234"


async def test_credential_must_belong_to_the_project_in_the_url(client: httpx.AsyncClient, register: RegisterFn) -> None:
    alice = await register()
    first = (await create_project(client, alice))["id"]
    second = (await create_project(client, alice, name="Other"))["id"]
    cid = (await add_credential(client, alice, first))["id"]

    assert (await client.delete(f"/projects/{second}/credentials/{cid}", headers=alice)).status_code == 404
    assert await Credential.count() == 1


# ---------- credentials ----------


async def test_credentials_are_encrypted_at_rest_and_never_returned(
    client: httpx.AsyncClient, register: RegisterFn
) -> None:
    headers = await register()
    pid = (await create_project(client, headers))["id"]

    res = await client.post(f"/projects/{pid}/credentials", json=CREDENTIAL, headers=headers)

    assert res.status_code == 201
    assert "password" not in res.json() and "demo1234" not in res.text
    listed = await client.get(f"/projects/{pid}/credentials", headers=headers)
    assert "demo1234" not in listed.text and "password" not in listed.text
    stored = await Credential.find_one()
    assert stored is not None
    assert "demo1234" not in stored.password_encrypted
    assert decrypt_secret(stored.password_encrypted) == "demo1234"


async def test_update_credential_password_re_encrypts(client: httpx.AsyncClient, register: RegisterFn) -> None:
    headers = await register()
    pid = (await create_project(client, headers))["id"]
    cid = (await add_credential(client, headers, pid, password="old-pass"))["id"]

    res = await client.patch(f"/projects/{pid}/credentials/{cid}", json={"password": "new-pass"}, headers=headers)

    assert res.status_code == 200 and res.json()["username"] == "demo@shop.test"
    stored = await Credential.find_one()
    assert stored is not None and decrypt_secret(stored.password_encrypted) == "new-pass"


async def test_projects_cannot_target_internal_services(client: httpx.AsyncClient, register: RegisterFn) -> None:
    headers = await register()
    for url in ("http://mongo:27017/", "http://API:8000/", "http://169.254.169.254/latest/", "http://[fe80::1]/"):
        r = await client.post("/projects", json={"name": "x", "base_url": url, "authorised_testing_confirmed": True},
                              headers=headers)
        assert r.status_code == 422, url
        assert "can't be tested" in r.text
    ok = await client.post("/projects", json={"name": "Local app", "base_url": "http://localhost:3000/",
                                              "authorised_testing_confirmed": True}, headers=headers)
    assert ok.status_code == 201  # local dev servers stay allowed
    r = await client.patch(f"/projects/{ok.json()['id']}", json={"base_url": "http://mongo:27017/"}, headers=headers)
    assert r.status_code == 422


async def test_allowed_target_hosts_restricts_projects(client: httpx.AsyncClient, register: RegisterFn,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "allowed_target_hosts", "sslip.io, demo-shop")
    headers = await register()

    async def create(url: str) -> int:
        r = await client.post("/projects", json={"name": "x", "base_url": url, "authorised_testing_confirmed": True},
                              headers=headers)
        return r.status_code

    assert await create("https://shop.1.2.3.4.sslip.io/") == 201
    assert await create("http://demo-shop:8000/") == 201
    assert await create("https://example.com/") == 422
    assert await create("https://evilsslip.io/") == 422  # suffix must match a whole label
