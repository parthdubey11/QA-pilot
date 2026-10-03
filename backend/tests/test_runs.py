import asyncio
import json
from pathlib import Path

import httpx
import pytest
from beanie import PydanticObjectId

from app.core.config import get_settings
from app.models.job import Job
from app.models.run import Run, RunStep
from app.models.test_case import TestCase
from app.routes import runs as runs_route
from tests.conftest import RegisterFn
from worker import main as worker

NEW_PROJECT = {"name": "Demo Shop", "base_url": "http://demo-shop:8000", "authorised_testing_confirmed": True}
RUN = {"goal": "Smoke test the home page"}


async def new_project(client: httpx.AsyncClient, headers: dict[str, str]) -> str:
    res = await client.post("/projects", json=NEW_PROJECT, headers=headers)
    assert res.status_code == 201
    return res.json()["id"]


async def start_run(client: httpx.AsyncClient, headers: dict[str, str], project_id: str, **body: object) -> dict:
    res = await client.post(f"/projects/{project_id}/runs", json=RUN | body, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


def parse_sse(text: str) -> list[tuple[str, dict]]:
    events = []
    for block in text.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if not line.startswith(":"))
        if "event" in fields:
            events.append((fields["event"], json.loads(fields["data"])))
    return events


@pytest.fixture(autouse=True)
def fast_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runs_route, "STREAM_POLL_SECONDS", 0.02)


@pytest.fixture
def storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(get_settings(), "storage_dir", str(tmp_path))
    return tmp_path


# ---------- creating runs ----------


async def test_create_run_queues_a_job(client: httpx.AsyncClient, register: RegisterFn) -> None:
    headers = await register()
    pid = await new_project(client, headers)

    run = await start_run(client, headers, pid, options={"mobile_viewport": True, "max_tests": 5})

    assert run["status"] == "queued" and run["project_name"] == "Demo Shop"
    assert run["options"] == {"accessibility": True, "mobile_viewport": True, "max_tests": 5}
    job = await Job.find_one()
    assert job is not None and job.type == "run" and job.payload == {"run_id": run["id"]} and job.status == "pending"
    listed = (await client.get(f"/projects/{pid}/runs", headers=headers)).json()
    assert [r["id"] for r in listed] == [run["id"]]


async def test_one_active_run_per_project(client: httpx.AsyncClient, register: RegisterFn) -> None:
    headers = await register()
    pid = await new_project(client, headers)
    first = await start_run(client, headers, pid)

    second = await client.post(f"/projects/{pid}/runs", json=RUN, headers=headers)
    assert second.status_code == 409

    stored = await Run.get(PydanticObjectId(first["id"]))
    assert stored is not None
    stored.status = "completed"
    await stored.save()
    assert (await client.post(f"/projects/{pid}/runs", json=RUN, headers=headers)).status_code == 201


async def test_run_input_is_validated(client: httpx.AsyncClient, register: RegisterFn) -> None:
    headers = await register()
    pid = await new_project(client, headers)

    blank = await client.post(f"/projects/{pid}/runs", json={"goal": "   "}, headers=headers)
    too_many = await client.post(f"/projects/{pid}/runs", json=RUN | {"options": {"max_tests": 50}}, headers=headers)

    assert blank.status_code == too_many.status_code == 422


async def test_runs_are_private_to_the_project_owner(client: httpx.AsyncClient, register: RegisterFn) -> None:
    alice = await register(email="alice@example.com")
    bob = await register(email="bob@example.com")
    pid = await new_project(client, alice)
    run_id = (await start_run(client, alice, pid))["id"]

    attempts = [
        await client.post(f"/projects/{pid}/runs", json=RUN, headers=bob),
        await client.get(f"/projects/{pid}/runs", headers=bob),
        await client.get(f"/runs/{run_id}", headers=bob),
        await client.get(f"/runs/{run_id}/steps", headers=bob),
        await client.get(f"/runs/{run_id}/stream", headers=bob),
        await client.get(f"/runs/{run_id}/steps/0/screenshot", headers=bob),
        await client.get("/runs/not-an-id", headers=bob),
    ]
    assert [r.status_code for r in attempts] == [404] * len(attempts)
    assert (await client.get(f"/runs/{run_id}")).status_code == 401


async def test_deleting_a_project_removes_its_runs(client: httpx.AsyncClient, register: RegisterFn) -> None:
    headers = await register()
    pid = await new_project(client, headers)
    run = await start_run(client, headers, pid)

    assert (await client.delete(f"/projects/{pid}", headers=headers)).status_code == 409  # run in progress

    stored = await Run.get(PydanticObjectId(run["id"]))
    assert stored is not None and stored.id is not None
    stored.status = "completed"
    await stored.save()
    await RunStep(run_id=stored.id, index=0, kind="info", message="hi").insert()
    assert (await client.delete(f"/projects/{pid}", headers=headers)).status_code == 204
    assert await Run.count() == 0 and await RunStep.count() == 0 and await Job.count() == 0


# ---------- steps, stream, screenshots ----------


async def add_steps(run_id: str, *messages: str, start: int = 0) -> None:
    for i, message in enumerate(messages, start=start):
        await RunStep(run_id=PydanticObjectId(run_id), index=i, kind="info", message=message).insert()


async def finish(run_id: str, status: str = "completed") -> None:
    run = await Run.get(PydanticObjectId(run_id))
    assert run is not None
    run.status = status  # type: ignore[assignment]
    await run.save()


async def test_steps_endpoint_supports_after(client: httpx.AsyncClient, register: RegisterFn) -> None:
    headers = await register()
    run_id = (await start_run(client, headers, await new_project(client, headers)))["id"]
    await add_steps(run_id, "a", "b", "c")

    all_steps = (await client.get(f"/runs/{run_id}/steps", headers=headers)).json()
    later = (await client.get(f"/runs/{run_id}/steps?after=0", headers=headers)).json()

    assert [s["message"] for s in all_steps] == ["a", "b", "c"]
    assert [s["index"] for s in later] == [1, 2]
    assert all_steps[0]["has_screenshot"] is False and "screenshot_path" not in all_steps[0]


async def test_stream_of_finished_run_sends_everything_then_ends(client: httpx.AsyncClient, register: RegisterFn) -> None:
    headers = await register()
    run_id = (await start_run(client, headers, await new_project(client, headers)))["id"]
    await add_steps(run_id, "opened", "snapshot")
    await finish(run_id)

    res = await client.get(f"/runs/{run_id}/stream", headers=headers)

    assert res.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(res.text)
    assert [e for e, _ in events] == ["run", "step", "step", "end"]
    assert events[0][1]["status"] == "completed"
    assert [d["message"] for e, d in events if e == "step"] == ["opened", "snapshot"]
    assert "id: 1" in res.text  # step index as the SSE id, for resuming


async def test_stream_delivers_live_steps_until_the_run_ends(client: httpx.AsyncClient, register: RegisterFn) -> None:
    headers = await register()
    run_id = (await start_run(client, headers, await new_project(client, headers)))["id"]
    await add_steps(run_id, "first")

    async def worker_side() -> None:
        await asyncio.sleep(0.1)
        await finish(run_id, "running")
        await add_steps(run_id, "second", "third", start=1)
        await asyncio.sleep(0.1)
        await finish(run_id, "completed")

    background = asyncio.create_task(worker_side())
    res = await asyncio.wait_for(client.get(f"/runs/{run_id}/stream", headers=headers), timeout=5)
    await background

    events = parse_sse(res.text)
    assert [d["message"] for e, d in events if e == "step"] == ["first", "second", "third"]
    assert [d["status"] for e, d in events if e == "run"] == ["queued", "running", "completed"]
    assert events[-1] == ("end", {"status": "completed"})


async def test_stream_resumes_after_last_event_id(client: httpx.AsyncClient, register: RegisterFn) -> None:
    headers = await register()
    run_id = (await start_run(client, headers, await new_project(client, headers)))["id"]
    await add_steps(run_id, "a", "b", "c")
    await finish(run_id)

    by_query = parse_sse((await client.get(f"/runs/{run_id}/stream?after=1", headers=headers)).text)
    by_header = parse_sse((await client.get(f"/runs/{run_id}/stream", headers=headers | {"Last-Event-ID": "0"})).text)

    assert [d["message"] for e, d in by_query if e == "step"] == ["c"]
    assert [d["message"] for e, d in by_header if e == "step"] == ["b", "c"]


async def test_screenshot_endpoint_serves_png_and_blocks_traversal(
    client: httpx.AsyncClient, register: RegisterFn, storage: Path
) -> None:
    headers = await register()
    run_id = (await start_run(client, headers, await new_project(client, headers)))["id"]
    shot = storage / "runs" / run_id / "step-0000.png"
    shot.parent.mkdir(parents=True)
    shot.write_bytes(b"\x89PNG fake")
    (storage.parent / "secret.png").write_bytes(b"secret")
    oid = PydanticObjectId(run_id)
    await RunStep(run_id=oid, index=0, kind="observation", message="shot",
                  screenshot_path=f"runs/{run_id}/step-0000.png").insert()
    await RunStep(run_id=oid, index=1, kind="observation", message="evil", screenshot_path="../secret.png").insert()

    ok = await client.get(f"/runs/{run_id}/steps/0/screenshot", headers=headers)
    evil = await client.get(f"/runs/{run_id}/steps/1/screenshot", headers=headers)
    missing = await client.get(f"/runs/{run_id}/steps/7/screenshot", headers=headers)

    assert ok.status_code == 200 and ok.headers["content-type"] == "image/png" and ok.content == b"\x89PNG fake"
    assert evil.status_code == missing.status_code == 404


# ---------- worker ----------


async def test_worker_startup_fails_interrupted_runs(client: httpx.AsyncClient, register: RegisterFn) -> None:
    headers = await register()
    run = await start_run(client, headers, await new_project(client, headers))
    await worker.claim_next_job()
    await finish(run["id"], "running")

    assert await worker.recover_interrupted() == 1

    stored = (await client.get(f"/runs/{run['id']}", headers=headers)).json()
    assert stored["status"] == "failed" and "worker stopped" in stored["error"]
    assert (await client.post(f"/projects/{stored['project_id']}/runs", json=RUN, headers=headers)).status_code == 201


# ---------- test cases ----------


async def add_test_case(run_id: str, index: int, title: str, **fields: object) -> TestCase:
    from app.models.base import utcnow

    run = await Run.get(PydanticObjectId(run_id))
    assert run is not None
    tc = TestCase(run_id=PydanticObjectId(run_id), project_id=run.project_id, index=index, title=title, type="happy",
                  steps=["Open the page"], expected="It works", updated_at=utcnow(), **fields)
    await tc.insert()
    return tc


async def test_test_cases_endpoint_and_stream_events(client: httpx.AsyncClient, register: RegisterFn) -> None:
    alice = await register(email="alice@example.com")
    bob = await register(email="bob@example.com")
    run_id = (await start_run(client, alice, await new_project(client, alice)))["id"]
    await add_test_case(run_id, 1, "Log in", status="passed", reason="Welcome shown")
    first = await add_test_case(run_id, 0, "Sign up", status="running")

    async def worker_side() -> None:
        await asyncio.sleep(0.1)
        from app.models.base import utcnow

        first.status, first.reason, first.updated_at = "failed", "Duplicate email accepted", utcnow()  # type: ignore[assignment]
        await first.save()
        await asyncio.sleep(0.1)
        await finish(run_id)

    background = asyncio.create_task(worker_side())
    res = await asyncio.wait_for(client.get(f"/runs/{run_id}/stream", headers=alice), timeout=5)
    await background

    listed = (await client.get(f"/runs/{run_id}/test-cases", headers=alice)).json()
    assert [(c["index"], c["title"], c["status"]) for c in listed] == [(0, "Sign up", "failed"), (1, "Log in", "passed")]
    assert "run_id" not in listed[0] and listed[1]["reason"] == "Welcome shown"
    events = [(e, d) for e, d in parse_sse(res.text) if e == "test_case"]
    assert [(d["index"], d["status"]) for _, d in events] == [(0, "running"), (1, "passed"), (0, "failed")]
    assert (await client.get(f"/runs/{run_id}/test-cases", headers=bob)).status_code == 404


async def test_daily_ai_run_limits_and_max_tests_cap(client: httpx.AsyncClient, register: RegisterFn,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import get_settings
    from app.models.run import Run

    settings = get_settings()
    monkeypatch.setattr(settings, "max_agent_runs_per_user_per_day", 1)
    monkeypatch.setattr(settings, "max_tests_limit", 5)
    alice = await register(email="alice@example.com")
    bob = await register(email="bob@example.com")

    async def new_project(headers: dict[str, str], name: str) -> str:
        r = await client.post("/projects", json={"name": name, "base_url": "http://shop.test",
                                                 "authorised_testing_confirmed": True}, headers=headers)
        return r.json()["id"]

    p1, p2 = await new_project(alice, "A1"), await new_project(alice, "A2")
    first = await client.post(f"/projects/{p1}/runs", json={"goal": "g", "options": {"max_tests": 15}}, headers=alice)
    assert first.status_code == 201 and first.json()["options"]["max_tests"] == 5  # capped by the server
    second = await client.post(f"/projects/{p2}/runs", json={"goal": "g"}, headers=alice)
    assert second.status_code == 429 and "today's 1 AI runs" in second.json()["detail"]

    # site-wide budget: bob's own limit isn't reached, but the server's is
    monkeypatch.setattr(settings, "max_agent_runs_per_user_per_day", 0)
    monkeypatch.setattr(settings, "max_agent_runs_per_day", 1)
    bob_run = await client.post(f"/projects/{await new_project(bob, 'B')}/runs", json={"goal": "g"}, headers=bob)
    assert bob_run.status_code == 429 and "AI run budget" in bob_run.json()["detail"]
    assert await Run.find(Run.kind == "agent").count() == 1


async def test_health_reports_public_limits(client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "registration_code", "letmein")
    body = (await client.get("/health")).json()
    assert body["registration_code_required"] is True and body["max_tests_limit"] == 15
