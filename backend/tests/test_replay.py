"""Saved tests: recording, locators, replay without LLM, healing, export, schedules, notifications."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from beanie import PydanticObjectId
from playwright.async_api import Browser, Route
from pymongo.asynchronous.database import AsyncDatabase

from app.models.run import Run
from app.models.saved_test import Locators, Notification, SavedStep, SavedTest, Schedule
from app.models.test_case import TestCase
from app.services.playwright_export import export_spec
from app.services.run_steps import StepRecorder
from tests.conftest import RegisterFn
from tests.test_agents import FakeLLM, browser, decide, ref_for, storage  # noqa: F401 - fixtures
from worker import main as worker
from worker.agents.executor import ExecutorDecision, HistoryItem
from worker.agents.healer import HealDecision
from worker.agents.judge import Verdict
from worker.agents.planner import TestPlan
from worker.browser import BrowserSession, SecretVault, Snapshot, SnapshotElement, TestCredential
from worker.browser.locators import describe_element, resolve
from worker.orchestrator import handle_run_job
from worker.recorder import pick_assertions, saved_steps
from worker.replayer import replay_test
from worker.scheduler import ScheduleSync, fire_schedule

SITE = "http://shop.test"
VAULT = SecretVault([TestCredential("Shopper", "demo@shop.test", "demo1234")])


def shop_pages(variant: str) -> dict[str, str]:
    v2 = variant == "v2"
    name_label, email_label = ("Full name", "Email address") if v2 else ("Name", "Email")
    name_id, email_id = ("full-name", "email-address") if v2 else ("name", "email")
    button_id, button_text = ("register", "Register") if v2 else ("create-account", "Create account")
    cart_id, cart_text = ("add-to-bag", "Add to bag") if v2 else ("add-to-cart", "Add to cart")
    return {
        "/": "<title>Shop</title><h1>Shop</h1><a href='/signup'>Sign up</a> <a href='/products/1'>Mug</a>",
        "/signup": f"""<title>Sign up</title><h1>Create an account</h1>
          <form onsubmit="event.preventDefault(); document.getElementById('msg').textContent =
            this.email.value.includes('@') ? 'Account created. You can now log in.' : 'Enter a valid email'">
            <label for="{name_id}">{name_label}</label> <input id="{name_id}" name="name">
            <label for="{email_id}">{email_label}</label> <input id="{email_id}" name="email">
            <button id="{button_id}">{button_text}</button></form><p role="status" id="msg"></p>""",
        "/products/1": f"""<title>Mug</title><h1>Mug</h1><p>$10.00</p>
          <button id="{cart_id}" style="margin:20px" onclick="document.getElementById('st').textContent = 'Added 1 to your cart.'">{cart_text}</button>
          <p role="status" id="st"></p>""",
    }


class Shop:
    """A route-served shop whose variant can be switched between v1 and v2 (renamed buttons/labels)."""

    def __init__(self, browser: Browser):
        self.browser = browser
        self.variant = "v1"
        self.sessions = 0

    async def serve(self, route: Route) -> None:
        url = route.request.url
        path = "/" + url.removeprefix(SITE).lstrip("/").split("?")[0]
        pages = shop_pages(self.variant)
        if url.startswith(SITE) and path in pages:
            await route.fulfill(status=200, content_type="text/html",
                                body=f"<!doctype html><html lang='en'><body>{pages[path]}</body></html>")
        else:
            await route.fulfill(status=404, body="not found")

    @asynccontextmanager
    async def session(self, base_url: str = SITE, *, vault: SecretVault | None = None, **_: Any) -> AsyncIterator[BrowserSession]:  # noqa: ANN401
        context = await self.browser.new_context()
        await context.route("**/*", self.serve)
        self.sessions += 1
        try:
            yield BrowserSession(await context.new_page(), allowed_origin=SITE, action_delay_ms=0, timeout_ms=4000,
                                 vault=vault or VAULT)
        finally:
            await context.close()


@pytest.fixture
def shop(browser: Browser) -> Shop:  # noqa: F811
    return Shop(browser)


def launcher_factory(shop: Shop):  # noqa: ANN201
    @asynccontextmanager
    async def factory():  # noqa: ANN202
        yield shop

    return factory


async def record_signup(session: BrowserSession) -> SavedTest:
    """A saved test for signup, with locators recorded on the v1 page (what the executor does)."""
    await session.goto("/signup")
    snap = await session.snapshot()

    async def loc(role: str, name: str) -> Locators:
        ref = next(e.ref for e in snap.elements if e.role == role and e.name == name)
        return await describe_element(session.page, ref, snap.find(ref))

    steps = [
        SavedStep(tool="type", locators=await loc("textbox", "Name"), text="Asha", note="Enter the name"),
        SavedStep(tool="type", locators=await loc("textbox", "Email"), text="{{cred:Shopper:username}}"),
        SavedStep(tool="click", locators=await loc("button", "Create account"), note="Submit the signup form"),
    ]
    saved = SavedTest(project_id=PydanticObjectId(), title="Sign up works", title_key="sign up works",
                      start_path="/signup", steps=steps, source_run_id=PydanticObjectId(),
                      assertions=[{"kind": "url", "value": "/signup"},
                                  {"kind": "text", "value": "Account created. You can now log in."}])
    await saved.insert()
    return saved


# ---------- recording ----------


def test_saved_steps_and_assertions() -> None:
    loc = Locators(role="button", name="Go", tag="button")
    history = [
        HistoryItem(1, "type", {"tool": "type", "ref": 3, "text": "{{cred:Shopper:username}}"}, "Typed", locators=loc),
        HistoryItem(2, "(same plan)", {"tool": "click", "ref": 4}, "Failed: No element", ok=False, locators=loc),
        HistoryItem(3, "look", {"tool": "snapshot"}, "took a new snapshot"),
        HistoryItem(4, "submit", {"tool": "press", "key": "Enter"}, "Pressed Enter"),
    ]
    steps = saved_steps(history)
    assert steps is not None and [s.tool for s in steps] == ["type", "press"]  # failed and snapshot steps dropped
    assert steps[0].text == "{{cred:Shopper:username}}" and steps[0].note == "type"
    assert saved_steps([HistoryItem(1, "t", {"tool": "click", "ref": 1}, "Clicked", locators=None)]) is None

    start = Snapshot(url=SITE + "/signup", title="", elements=[SnapshotElement(ref=1, role="heading", name="Sign up")])
    final = Snapshot(url=SITE + "/signup?x=1", title="", elements=[
        SnapshotElement(ref=1, role="heading", name="Sign up"),
        SnapshotElement(ref=2, role="text", text="Hello demo@shop.test"),
        SnapshotElement(ref=3, role="status", name="", text="Account created. You can now log in."),
        SnapshotElement(ref=4, role="heading", name="Welcome"),
    ])
    assertions = [(a.kind, a.value) for a in pick_assertions(start, final, VAULT.redact)]
    assert assertions == [("url", "/signup"), ("text", "Account created. You can now log in."), ("text", "Welcome")]


# ---------- locators & replay ----------


async def test_locators_survive_renames_with_guarded_fallbacks(shop: Shop) -> None:
    async with shop.session() as session:
        await session.goto("/products/1")
        snap = await session.snapshot()
        ref = next(e.ref for e in snap.elements if e.name == "Add to cart")
        loc = await describe_element(session.page, ref, snap.find(ref))
        assert (loc.role, loc.name, loc.text, loc.css, loc.tag) == ("button", "Add to cart", "Add to cart", "#add-to-cart", "button")
        assert loc.x and loc.y

        _, method = await resolve(session.page, loc)
        assert method is None  # primary locator (role+name) works on v1

        shop.variant = "v2"
        await session.goto("/products/1")
        found, method = await resolve(session.page, loc)
        assert method == "position" and await found.inner_text() == "Add to bag"  # same place, similar name

        unrelated = loc.model_copy(update={"name": "Delete my account", "text": "Delete my account"})
        assert (await resolve(session.page, unrelated)) == (None, None)  # not accepted: name too different


async def test_replay_without_llm_passes_and_is_fast(shop: Shop, database: AsyncDatabase, storage) -> None:  # noqa: ANN001, F811
    async with shop.session() as session:
        saved = await record_signup(session)
    run = Run(project_id=saved.project_id, created_by=PydanticObjectId(), goal="replay", kind="replay")
    await run.insert()
    assert run.id is not None
    tc = TestCase(run_id=run.id, project_id=run.project_id, index=0, title=saved.title, type="happy", steps=[], expected="")

    async with shop.session() as session:
        outcome = await replay_test(session, saved, tc, StepRecorder(run), run.id, get_llm=lambda: None)
        typed = await session.page.locator("input[name=email]").input_value()

    assert outcome.passed and outcome.heals == [] and outcome.steps_done == 3, outcome.reason
    assert typed == "demo@shop.test"  # credential placeholder filled at replay time


async def test_healer_repairs_renamed_elements_and_records_history(shop: Shop, database: AsyncDatabase, storage) -> None:  # noqa: ANN001, F811
    async with shop.session() as session:
        saved = await record_signup(session)
    shop.variant = "v2"  # "Name"->"Full name", "Email"->"Email address", "Create account"->"Register", new ids
    run = Run(project_id=saved.project_id, created_by=PydanticObjectId(), goal="replay", kind="replay")
    await run.insert()
    assert run.id is not None
    tc = TestCase(run_id=run.id, project_id=run.project_id, index=0, title=saved.title, type="happy", steps=[], expected="")
    llm = FakeLLM({HealDecision: [lambda p: HealDecision(ref=ref_for(p, "button", "Register"),
                                                           reason="'Create account' was renamed to 'Register'")]})

    async with shop.session() as session:
        outcome = await replay_test(session, saved, tc, StepRecorder(run), run.id, get_llm=lambda: llm)

    assert outcome.passed, outcome.reason
    methods = [(h.step_index, h.method) for h in outcome.heals]
    assert methods == [(0, "fallback-locator"), (1, "fallback-locator"), (2, "ai-healer")]
    assert outcome.heals[0].old.name == "Name" and outcome.heals[0].new.name == "Full name"  # found by css name=
    assert outcome.heals[2].old.name == "Create account" and outcome.heals[2].new.name == "Register"
    assert saved.steps[2].locators is not None and saved.steps[2].locators.css == "#register"  # step repaired
    prompt = llm.prompts(HealDecision)[0]
    assert 'button "Create account"' in prompt and "Submit the signup form" in prompt and "demo@shop.test" not in prompt

    # Next replay of the repaired test needs no healing at all.
    tc2 = tc.model_copy(update={"index": 1})
    async with shop.session() as session:
        again = await replay_test(session, saved, tc2, StepRecorder(run, 100), run.id, get_llm=lambda: None)
    assert again.passed and again.heals == []


async def test_healer_rejects_bad_picks_and_replay_fails_clearly(shop: Shop, database: AsyncDatabase, storage) -> None:  # noqa: ANN001, F811
    async with shop.session() as session:
        saved = await record_signup(session)
    shop.variant = "v2"
    run = Run(project_id=saved.project_id, created_by=PydanticObjectId(), goal="replay", kind="replay")
    await run.insert()
    assert run.id is not None
    tc = TestCase(run_id=run.id, project_id=run.project_id, index=0, title=saved.title, type="happy", steps=[], expected="")

    async with shop.session() as session:
        no_llm = await replay_test(session, saved, tc, StepRecorder(run), run.id, get_llm=lambda: None)
    assert not no_llm.passed and "no LLM is configured" in no_llm.reason and no_llm.steps_done == 2

    saved.steps[0].tool = "type"
    none = FakeLLM({HealDecision: [HealDecision(ref=None, reason="No submit button on the page")]})
    async with shop.session() as session:
        failed = await replay_test(session, saved, tc, StepRecorder(run, 50), run.id, get_llm=lambda: none)
    assert not failed.passed and "healer found no match: No submit button on the page" in failed.reason


# ---------- whole flow: agent run saves, replay run replays & heals ----------


async def new_project(client: httpx.AsyncClient, headers: dict[str, str]) -> str:
    pid = (await client.post("/projects", json={"name": "Shop", "base_url": SITE, "authorised_testing_confirmed": True},
                             headers=headers)).json()["id"]
    await client.post(f"/projects/{pid}/credentials", json={"label": "Shopper", "username": "demo@shop.test",
                                                           "password": "demo1234"}, headers=headers)
    return pid


async def test_passing_test_is_saved_then_replayed_and_healed(
    client: httpx.AsyncClient, register: RegisterFn, shop: Shop, storage  # noqa: ANN001, F811
) -> None:
    headers = await register()
    pid = await new_project(client, headers)
    await client.post(f"/projects/{pid}/runs", json={"goal": "signup", "options": {"max_tests": 1, "accessibility": False}},
                      headers=headers)
    plan = TestPlan.model_validate({"test_cases": [{"title": "Sign up with valid details", "type": "happy",
                                                    "start_path": "/signup", "steps": ["Fill", "Submit"],
                                                    "expected": "Account created"}]})
    llm = FakeLLM({
        TestPlan: [plan],
        ExecutorDecision: [
            lambda p: decide("Fill and submit the form",
                             {"tool": "type", "ref": ref_for(p, "textbox", "Name"), "text": "Asha"},
                             {"tool": "type", "ref": ref_for(p, "textbox", "Email"), "text": "{{cred:Shopper:username}}"},
                             {"tool": "click", "ref": ref_for(p, "button", "Create account")}),
            lambda p: decide("It says Account created", tool="done", result="pass", reason="created"),
        ],
        Verdict: [Verdict(result="pass", reason="Account created shown")],
    })
    job = await worker.claim_next_job()
    assert job is not None
    await handle_run_job(job, launcher_factory=launcher_factory(shop), llm=llm)

    tests = (await client.get(f"/projects/{pid}/saved-tests", headers=headers)).json()
    assert len(tests) == 1 and tests[0]["title"] == "Sign up with valid details"
    saved = tests[0]
    assert [s["tool"] for s in saved["steps"]] == ["type", "type", "click"]
    assert saved["steps"][1]["text"] == "{{cred:Shopper:username}}"  # never the real value
    assert {"kind": "text", "value": "Account created. You can now log in."} in saved["assertions"]
    assert saved["steps"][2]["locators"]["css"] == "#create-account"

    # Replay after the UI changed: fallbacks + healer repair it, heal history recorded, no other LLM calls.
    shop.variant = "v2"
    res = await client.post(f"/projects/{pid}/replays", json={}, headers=headers)
    assert res.status_code == 201 and res.json()["kind"] == "replay" and res.json()["trigger"] == "manual"
    heal_llm = FakeLLM({HealDecision: [lambda p: HealDecision(ref=ref_for(p, "button", "Register"), reason="renamed")]})
    job = await worker.claim_next_job()
    assert job is not None
    await handle_run_job(job, launcher_factory=launcher_factory(shop), llm=heal_llm)

    run = (await client.get(f"/runs/{res.json()['id']}", headers=headers)).json()
    assert run["status"] == "completed" and run["stats"]["passed"] == 1 and run["stats"]["healed_steps"] == 3
    assert run["stats"]["llm_calls"] == 1  # only the healer, only for the renamed button
    after = (await client.get(f"/saved-tests/{saved['id']}", headers=headers)).json()
    assert after["replays"] == 1 and after["last_result"] == "passed" and len(after["heal_history"]) == 3
    assert after["heal_history"][2]["method"] == "ai-healer" and after["steps"][2]["locators"]["name"] == "Register"
    steps = (await client.get(f"/runs/{run['id']}/steps", headers=headers)).json()
    assert any(s["phase"] == "heal" and "Create account" in s["message"] and "Register" in s["message"] for s in steps)
    cases = (await client.get(f"/runs/{run['id']}/test-cases", headers=headers)).json()
    assert cases[0]["status"] == "passed" and cases[0]["has_final_screenshot"]


# ---------- export ----------


def test_export_playwright_spec(database: AsyncDatabase) -> None:
    saved = SavedTest(
        project_id=PydanticObjectId(), title='Log in "happy" path', title_key="x", start_path="/login",
        source_run_id=PydanticObjectId(),
        steps=[SavedStep(tool="type", locators=Locators(role="textbox", name="Email", tag="input"),
                         text="{{cred:Test shopper:username}}", note="Type the email"),
               SavedStep(tool="type", locators=Locators(css='input[name="password"]', tag="input"),
                         text="pre-`${x}`-{{cred:Test shopper:password}}"),
               SavedStep(tool="click", locators=Locators(text="Log in", tag="button"), url_after="/"),
               SavedStep(tool="press", key="Enter", url_after="/")],
        assertions=[{"kind": "url", "value": "/"}, {"kind": "text", "value": "Hi, Demo User"}],
    )

    spec = export_spec(saved, "http://demo-shop:8000/")

    assert "import { test, expect } from '@playwright/test'" in spec
    assert 'const BASE_URL = process.env.QA_BASE_URL ?? "http://demo-shop:8000"' in spec
    assert 'test("Log in \\"happy\\" path", async ({ page }) => {' in spec
    assert "await page.goto(BASE_URL + \"/login\")" in spec
    assert ('await page.getByRole("textbox", { name: "Email", exact: true })'
            ".fill(`${process.env.QA_CRED_TEST_SHOPPER_USERNAME ?? ''}`)") in spec
    assert "`pre-\\`\\${x}\\`-${process.env.QA_CRED_TEST_SHOPPER_PASSWORD ?? ''}`" in spec  # escaped, not injected
    assert ('await page.getByText("Log in", { exact: true }).click()\n'
            '  await page.waitForURL((url) => url.pathname + url.search === "/")') in spec  # waits for the redirect
    assert "await page.keyboard.press(\"Enter\")\n  await page.waitForLoadState('networkidle')" in spec  # stayed on "/"
    assert 'await page.keyboard.press("Enter")' in spec and "// Type the email" in spec
    assert 'await expect(page.getByText("Hi, Demo User").first()).toBeVisible()' in spec
    assert "demo1234" not in spec and "QA_CRED_TEST_SHOPPER_PASSWORD" in spec.splitlines()[2]


async def test_saved_tests_api_export_and_isolation(client: httpx.AsyncClient, register: RegisterFn) -> None:
    alice = await register(email="alice@example.com")
    bob = await register(email="bob@example.com")
    pid = await new_project(client, alice)
    saved = SavedTest(project_id=PydanticObjectId(pid), title="Add to cart", title_key="add to cart", start_path="/",
                      steps=[SavedStep(tool="click", locators=Locators(role="button", name="Add to cart", tag="button"))],
                      source_run_id=PydanticObjectId())
    await saved.insert()

    res = await client.get(f"/saved-tests/{saved.id}/export.spec.ts", headers=alice)
    assert res.status_code == 200 and res.headers["content-disposition"] == 'attachment; filename="add-to-cart.spec.ts"'
    assert 'getByRole("button", { name: "Add to cart", exact: true }).click()' in res.text
    for r in (await client.get(f"/saved-tests/{saved.id}", headers=bob),
              await client.get(f"/saved-tests/{saved.id}/export.spec.ts", headers=bob),
              await client.get(f"/projects/{pid}/saved-tests", headers=bob),
              await client.post(f"/projects/{pid}/replays", json={}, headers=bob)):
        assert r.status_code == 404
    assert (await client.post(f"/projects/{pid}/replays", json={"saved_test_ids": [str(PydanticObjectId())]},
                              headers=alice)).status_code == 404
    assert (await client.post(f"/projects/{pid}/replays", json={}, headers=alice)).status_code == 201
    assert (await client.post(f"/projects/{pid}/replays", json={}, headers=alice)).status_code == 409  # one at a time
    assert (await client.delete(f"/saved-tests/{saved.id}", headers=alice)).status_code == 204
    empty = await new_project(client, alice)
    assert (await client.post(f"/projects/{empty}/replays", json={}, headers=alice)).status_code == 422


# ---------- schedules & notifications ----------


async def test_schedules_api(client: httpx.AsyncClient, register: RegisterFn) -> None:
    alice = await register(email="alice@example.com")
    bob = await register(email="bob@example.com")
    pid = await new_project(client, alice)

    bad = await client.post(f"/projects/{pid}/schedules", json={"name": "x", "cron": "61 * * * *"}, headers=alice)
    four = await client.post(f"/projects/{pid}/schedules", json={"name": "x", "cron": "0 9 * *"}, headers=alice)
    assert bad.status_code == four.status_code == 422
    res = await client.post(f"/projects/{pid}/schedules", json={"name": "Weekday mornings", "cron": "0  9 * * 1-5"},
                            headers=alice)
    assert res.status_code == 201
    schedule = res.json()
    assert schedule["cron"] == "0 9 * * 1-5" and schedule["enabled"] and schedule["next_run_at"]
    assert "T09:00:00" in schedule["next_run_at"]

    off = await client.patch(f"/schedules/{schedule['id']}", json={"enabled": False}, headers=alice)
    assert off.json()["enabled"] is False and off.json()["next_run_at"] is None
    assert (await client.patch(f"/schedules/{schedule['id']}", json={"cron": "bad"}, headers=alice)).status_code == 422
    assert (await client.patch(f"/schedules/{schedule['id']}", json={"enabled": True}, headers=bob)).status_code == 404
    assert (await client.get(f"/projects/{pid}/schedules", headers=bob)).status_code == 404
    assert len((await client.get(f"/projects/{pid}/schedules", headers=alice)).json()) == 1
    assert (await client.delete(f"/schedules/{schedule['id']}", headers=alice)).status_code == 204
    assert await Schedule.count() == 0


async def test_scheduler_sync_and_fire(client: httpx.AsyncClient, register: RegisterFn) -> None:
    headers = await register()
    pid = await new_project(client, headers)
    schedule = (await client.post(f"/projects/{pid}/schedules", json={"name": "Hourly", "cron": "0 * * * *"},
                                  headers=headers)).json()

    class FakeScheduler:
        def __init__(self) -> None:
            self.jobs: dict[str, Any] = {}

        def add_job(self, func: Any, trigger: Any, id: str, **_: Any) -> None:  # noqa: A002, ANN401
            self.jobs[id] = trigger

        def get_job(self, job_id: str) -> Any:  # noqa: ANN401
            return self.jobs.get(job_id)

        def remove_job(self, job_id: str) -> None:
            del self.jobs[job_id]

    fake = FakeScheduler()
    sync = ScheduleSync(fake)  # type: ignore[arg-type]
    await sync.sync()
    assert list(fake.jobs) == [schedule["id"]]
    await client.patch(f"/schedules/{schedule['id']}", json={"enabled": False}, headers=headers)
    await sync.sync()
    assert fake.jobs == {}
    await client.patch(f"/schedules/{schedule['id']}", json={"enabled": True}, headers=headers)

    await fire_schedule(schedule["id"])  # no saved tests yet
    stored = await Schedule.get(PydanticObjectId(schedule["id"]))
    assert stored is not None and stored.last_skip_reason == "Skipped: the project has no saved tests yet."

    await SavedTest(project_id=PydanticObjectId(pid), title="t", title_key="t", start_path="/", source_run_id=PydanticObjectId(),
                    steps=[SavedStep(tool="press", key="Tab")]).insert()
    await fire_schedule(schedule["id"])
    run = await Run.find_one(Run.trigger == "schedule")
    assert run is not None and run.kind == "replay" and str(run.schedule_id) == schedule["id"]
    await fire_schedule(schedule["id"])  # the first run is still queued
    stored = await Schedule.get(PydanticObjectId(schedule["id"]))
    assert stored is not None and "still in progress" in (stored.last_skip_reason or "")
    assert await Run.find(Run.trigger == "schedule").count() == 1


async def test_scheduled_run_with_new_failure_notifies(
    client: httpx.AsyncClient, register: RegisterFn, shop: Shop, storage  # noqa: ANN001, F811
) -> None:
    headers = await register()
    pid = await new_project(client, headers)
    await SavedTest(project_id=PydanticObjectId(pid), title="Mug page shows price", title_key="m", start_path="/products/1",
                    steps=[SavedStep(tool="wait", ms=10)], source_run_id=PydanticObjectId(),
                    assertions=[{"kind": "text", "value": "$12.00"}]).insert()  # the page shows $10.00: will fail
    schedule = (await client.post(f"/projects/{pid}/schedules", json={"name": "Nightly", "cron": "0 2 * * *"},
                                  headers=headers)).json()
    await fire_schedule(schedule["id"])
    job = await worker.claim_next_job()
    assert job is not None
    await handle_run_job(job, launcher_factory=launcher_factory(shop), llm=None)

    notes = (await client.get("/notifications", headers=headers)).json()
    assert notes["unread"] == 1
    note = notes["items"][0]
    assert note["kind"] == "new_failures" and note["title"] == "1 new failure(s) in Shop"
    assert "Mug page shows price" in note["body"] and note["run_id"]

    # Same failure again next time: not "new", no second notification.
    await fire_schedule(schedule["id"])
    job = await worker.claim_next_job()
    assert job is not None
    await handle_run_job(job, launcher_factory=launcher_factory(shop), llm=None)
    assert (await client.get("/notifications", headers=headers)).json()["unread"] == 1

    other = await register(email="other@example.com")
    assert (await client.post(f"/notifications/{note['id']}/read", headers=other)).status_code == 404
    assert (await client.post(f"/notifications/{note['id']}/read", headers=headers)).status_code == 204
    assert (await client.get("/notifications", headers=headers)).json()["unread"] == 0
    await Notification(user_id=(await Run.find_one()).created_by, project_id=PydanticObjectId(pid), kind="run_failed",  # type: ignore[union-attr]
                       title="t", body="b").insert()
    assert (await client.post("/notifications/read-all", headers=headers)).status_code == 204
    assert (await client.get("/notifications", headers=headers)).json()["unread"] == 0


async def test_unlabelled_field_found_by_css_is_not_a_heal(shop: Shop) -> None:
    async with shop.session() as session:
        await session.page.set_content("<input id='q' name='q'><button id='go'>Go</button>")
        loc = Locators(css="#q", tag="input", x=10, y=10)  # no role/name/text: CSS is its first locator
        found, method = await resolve(session.page, loc)
        assert found is not None and method is None
        renamed = Locators(role="button", name="Go", text="Go", css="#go", tag="button")
        await session.page.set_content("<button id='go'>Go now</button>")
        found, method = await resolve(session.page, renamed)
        assert found is not None and method == "css"  # role+name and text failed first: a real heal


def test_export_keeps_llm_text_inside_comments(database: AsyncDatabase) -> None:
    # Notes/titles come from the LLM and may echo the target site: a line break must not end the comment.
    saved = SavedTest(
        project_id=PydanticObjectId(), title="Evil\nrequire('child_process').exec('calc')", title_key="x", start_path="/",
        source_run_id=PydanticObjectId(),
        steps=[SavedStep(tool="press", key="Enter", note="ok fetch('https://evil.test')\r\nstill a comment")],
    )

    spec = export_spec(saved, "http://shop.test/")

    assert "// Saved test: Evil require('child_process').exec('calc')" in spec
    assert "  // ok fetch('https://evil.test') still a comment\n" in spec
    assert 'test("Evil\\nrequire' in spec  # the test name is a JSON string literal: newline escaped
    assert all(line.lstrip().startswith(("//", 'test("Evil')) for line in spec.splitlines() if "evil" in line.lower())
