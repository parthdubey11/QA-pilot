"""Explorer / Planner / Executor / Judge / orchestrator with a mocked LLM and a real browser.

The "site" is a tiny shop served by page.route (no server). The FakeLLM reads refs out of the snapshot in the
prompt, the way a real model would. Skipped without a browser; locally set BROWSER_CHANNEL=msedge.
"""

import os
import re
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from playwright.async_api import Browser, Route, async_playwright
from pydantic import BaseModel
from pymongo.asynchronous.database import AsyncDatabase

from app.models.run import Run, RunStep
from app.models.test_case import TestCase
from app.services.run_steps import StepRecorder
from tests.conftest import RegisterFn
from worker import main as worker
from worker.agents.executor import ExecutionResult, ExecutorDecision, execute_test
from worker.agents.explorer import explore
from worker.agents.judge import Verdict, judge_test
from worker.agents.planner import TestPlan, plan_tests
from worker.browser import BrowserSession, SecretVault, TestCredential
from worker.browser.tools import Done
from worker.llm import LLMError, LLMOutputError, LLMProvider
from worker.orchestrator import handle_run_job

SITE = "http://shop.test"
NAV = '<nav><a href="/">Home</a> <a href="/signup">Sign up</a> <a href="/login">Log in</a> <a href="/cart">Cart</a> ' \
      '<a href="/logout">Log out</a> <a href="https://elsewhere.example/">Partner</a></nav>'
PAGES = {
    "/": f"<title>Shop</title>{NAV}<h1>Products</h1><a href='/products/1'>Mug</a><p>Free delivery</p>",
    "/products/1": f"<title>Mug</title>{NAV}<h1>Mug</h1><p>$10.00</p><a href='/products/1/reviews'>Reviews</a>"
                   "<button>Add to cart</button>",
    "/products/1/reviews": f"<title>Reviews</title>{NAV}<h1>Reviews</h1>",
    "/signup": f"""<title>Sign up</title>{NAV}<h1>Create an account</h1>
        <form onsubmit="event.preventDefault(); document.getElementById('msg').textContent =
          this.email.value.includes('@') ? 'Account created' : 'Enter a valid email'">
          <label>Name <input name="name" required></label>
          <label>Email <input name="email" required></label>
          <label>Password <input type="password" name="password" required></label>
          <button>Create account</button></form><p role="status" id="msg"></p>""",
    "/login": f"""<title>Log in</title>{NAV}<h1>Log in</h1>
        <form onsubmit="event.preventDefault(); document.getElementById('msg').textContent =
          (this.email.value === 'demo@shop.test' && this.password.value === 'demo1234') ? 'Welcome back' : 'Invalid login'">
          <label>Email <input name="email"></label> <label>Password <input type="password" name="password"></label>
          <button>Log in</button></form><p role="status" id="msg"></p>""",
    "/cart": f"<title>Cart</title>{NAV}<h1>Your cart</h1><p>Your cart is empty.</p>",
}
PNG_MAGIC = b"\x89PNG"


# ---------- fakes ----------


def ref_for(prompt: str, role: str, name: str) -> int:
    """Find [N] for `role "name"` in the snapshot part of a prompt (what a real model does)."""
    snapshot = prompt.split("Current page snapshot")[-1]
    match = re.search(rf'\[(\d+)\] {role} "{re.escape(name)}"', snapshot)
    assert match, f'{role} "{name}" not in snapshot:\n{snapshot}'
    return int(match.group(1))


Reply = BaseModel | Exception | Callable[[str], BaseModel | dict]


class FakeLLM(LLMProvider):
    """generate_json returns scripted replies per schema and records every call."""

    name = "fake"

    def __init__(self, script: dict[type[BaseModel], list[Reply]]):
        super().__init__("fake-model", http=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))))
        self.script = {schema: list(replies) for schema, replies in script.items()}
        self.calls: list[tuple[type[BaseModel], str, list[bytes]]] = []

    async def generate_json(self, prompt: str, schema: type, images: list[bytes] | None = None,  # noqa: ANN401
                            *, schema_hint: str | None = None) -> Any:
        self.calls.append((schema, prompt, images or []))
        self.usage.calls += 1
        self.usage.input_tokens += len(prompt) // 4
        replies = self.script.get(schema) or []
        reply = replies.pop(0) if len(replies) > 1 else (replies[0] if replies else None)
        if reply is None:
            raise AssertionError(f"No scripted reply for {schema.__name__}")
        if isinstance(reply, Exception):
            raise reply
        value = reply(prompt) if callable(reply) and not isinstance(reply, BaseModel) else reply
        return value if isinstance(value, BaseModel) else schema.model_validate(value)

    async def _complete(self, prompt, schema, images):  # noqa: ANN001
        raise NotImplementedError

    def prompts(self, schema: type) -> list[str]:
        return [p for s, p, _ in self.calls if s is schema]


def decide(thought: str, *batch: dict, **action: Any) -> dict:  # noqa: ANN401
    """An executor reply: one action as keyword arguments, or several as dicts."""
    return {"thought": thought, "actions": list(batch) or [action]}


# ---------- browser fixtures ----------


async def serve(route: Route) -> None:
    url = route.request.url
    if not url.startswith(SITE):
        await route.abort()
        return
    path = "/" + url.removeprefix(SITE).lstrip("/").split("?")[0]
    if path in PAGES:
        await route.fulfill(status=200, content_type="text/html", body=f"<!doctype html><html lang='en'><body>{PAGES[path]}</body></html>")
    else:
        await route.fulfill(status=404, content_type="text/html", body="<title>Not found</title><h1>Not found</h1>")


@pytest.fixture
async def browser() -> AsyncIterator[Browser]:
    async with async_playwright() as pw:
        try:
            b = await pw.chromium.launch(channel=os.getenv("BROWSER_CHANNEL") or None)
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"No browser available ({str(exc).splitlines()[0]}); set BROWSER_CHANNEL=msedge locally")
        yield b
        await b.close()


class FakeSiteLauncher:
    """Same interface as BrowserLauncher, but every context is served by `serve` (and requests are logged)."""

    def __init__(self, browser: Browser):
        self.browser = browser
        self.requests: list[str] = []
        self.sessions = 0

    @asynccontextmanager
    async def session(self, base_url: str, *, vault: SecretVault | None = None, **_: Any) -> AsyncIterator[BrowserSession]:  # noqa: ANN401
        context = await self.browser.new_context()

        async def logged(route: Route) -> None:
            self.requests.append(f"{route.request.method} {route.request.url}")
            await serve(route)

        await context.route("**/*", logged)
        self.sessions += 1
        try:
            yield BrowserSession(await context.new_page(), allowed_origin=SITE, action_delay_ms=0, timeout_ms=5000,
                                 vault=vault)
        finally:
            await context.close()


@pytest.fixture
def launcher(browser: Browser) -> FakeSiteLauncher:
    return FakeSiteLauncher(browser)


VAULT = SecretVault([TestCredential("Shopper", "demo@shop.test", "demo1234")])


async def new_run_and_test(database: AsyncDatabase, **test: Any) -> tuple[StepRecorder, TestCase]:  # noqa: ANN401
    from beanie import PydanticObjectId

    run = Run(project_id=PydanticObjectId(), created_by=PydanticObjectId(), goal="test signup")
    await run.insert()
    assert run.id is not None
    tc = TestCase(run_id=run.id, project_id=run.project_id, index=0, title=test.get("title", "Sign up works"),
                  type="happy", start_path=test.get("start_path", "/signup"),
                  steps=["Fill the form", "Click Create account"], expected=test.get("expected", "Account created"))
    await tc.insert()
    return StepRecorder(run), tc


# ---------- explorer ----------


async def test_explorer_builds_site_map_without_side_effects(launcher: FakeSiteLauncher) -> None:
    seen: list[str] = []

    async def on_page(page, snapshot) -> None:  # noqa: ANN001
        seen.append(page.path)

    async with launcher.session(SITE) as session:
        site_map = await explore(session, SITE, max_pages=10, max_depth=1, on_page=on_page)

    paths = [p.path for p in site_map.pages]
    assert paths[0] == "/" and set(paths) == {"/", "/signup", "/login", "/cart", "/products/1"}
    assert seen == paths
    assert "/logout" not in paths  # destructive-looking link skipped
    assert "/products/1/reviews" not in paths  # beyond max_depth=1
    signup = next(p for p in site_map.pages if p.path == "/signup")
    assert [f.label for f in signup.forms[0].fields] == ["Name", "Email", "Password"]
    assert signup.forms[0].fields[0].required and signup.forms[0].buttons == ["Create account"]
    product = next(p for p in site_map.pages if p.path == "/products/1")
    assert product.buttons == ["Add to cart"] and "$10.00" in product.texts
    assert all(r.startswith("GET ") for r in launcher.requests)  # never submitted anything
    assert not any("elsewhere.example" in r for r in launcher.requests)

    text = site_map.to_text()
    assert 'form 1: textbox "Name" required, textbox "Email" required, textbox "Password" required; buttons: Create account' in text
    assert text.count('"Sign up" → /signup') == 1  # nav links listed once, not on every page


async def test_explorer_respects_page_limit(launcher: FakeSiteLauncher) -> None:
    async with launcher.session(SITE) as session:
        site_map = await explore(session, SITE, max_pages=2, max_depth=3)
    assert len(site_map.pages) == 2


# ---------- planner ----------


async def test_planner_prompt_and_truncation() -> None:
    tests = [{"title": f"Test {i}", "type": "edge", "start_path": "http://shop.test/signup?x=1",
              "steps": ["Open signup"], "expected": "Error shown"} for i in range(5)]
    llm = FakeLLM({TestPlan: [TestPlan.model_validate({"test_cases": tests})]})

    planned = await plan_tests(llm, goal="test signup and cart", site_map_text="Site map of SHOP-MAP", max_tests=3,
                               credential_placeholders=VAULT.placeholders)

    assert [t.title for t in planned] == ["Test 0", "Test 1", "Test 2"]
    assert planned[0].start_path == "/signup?x=1"  # only the path is kept
    prompt = llm.prompts(TestPlan)[0]
    for expected in ("test signup and cart", "SHOP-MAP", "at most 3 test cases", "{{cred:Shopper:password}}",
                     "empty required fields", "boundary numbers", "duplicate actions", "back-button",
                     "Do NOT use security attack strings"):
        assert expected in prompt, expected
    assert "demo1234" not in prompt


# ---------- executor ----------


async def test_executor_observe_think_act_loop(launcher: FakeSiteLauncher, database: AsyncDatabase, storage) -> None:  # noqa: ANN001
    steps, tc = await new_run_and_test(database)
    llm = FakeLLM({ExecutorDecision: [
        lambda p: decide("Fill in the name", tool="type", ref=ref_for(p, "textbox", "Name"), text="Asha"),
        lambda p: decide("Fill in the email", tool="type", ref=ref_for(p, "textbox", "Email"), text="asha@example.com"),
        lambda p: decide("Fill in the password", tool="type", ref=ref_for(p, "textbox", "Password"), text="Sup3r-Secret!"),
        lambda p: decide("Submit the form", tool="click", ref=ref_for(p, "button", "Create account")),
        lambda p: decide("The status says Account created", tool="done", result="pass", reason="Account created shown"),
    ]})

    async with launcher.session(SITE, vault=VAULT) as session:
        result = await execute_test(llm, session, steps, tc, max_steps=10, timeout_seconds=60,
                                    credential_placeholders=VAULT.placeholders)

    assert result.done == Done(result="pass", reason="Account created shown") and result.error is None
    assert result.steps_used == 5
    assert result.final_snapshot is not None and "Account created" in result.final_snapshot.to_text()
    assert result.final_screenshot is not None and result.final_screenshot.startswith(PNG_MAGIC)
    saved = await RunStep.find_all().sort(+RunStep.index).to_list()
    assert [s.kind for s in saved] == ["action"] + ["thought", "action"] * 4 + ["thought", "info"]
    assert all(s.phase == "execute" and s.test_case_index == 0 for s in saved)
    actions = [s for s in saved if s.kind == "action"]
    assert all(s.screenshot_path and (storage / s.screenshot_path).is_file() for s in actions)
    assert saved[1].message == "Fill in the name" and saved[1].snapshot and "[" in saved[1].snapshot
    assert "action 1 of at most 10" in llm.prompts(ExecutorDecision)[0]
    assert "Submit the form → click" in llm.prompts(ExecutorDecision)[4]  # history reaches the model


async def test_executor_fills_credential_placeholders_but_never_leaks_them(
    launcher: FakeSiteLauncher, database: AsyncDatabase, storage  # noqa: ANN001
) -> None:
    steps, tc = await new_run_and_test(database, start_path="/login", expected="Welcome back")
    llm = FakeLLM({ExecutorDecision: [
        lambda p: decide("Type the email", tool="type", ref=ref_for(p, "textbox", "Email"), text="{{cred:Shopper:username}}"),
        lambda p: decide("Type the password", tool="type", ref=ref_for(p, "textbox", "Password"), text="{{cred:Shopper:password}}"),
        lambda p: decide("Log in", tool="click", ref=ref_for(p, "button", "Log in")),
        lambda p: decide("Welcome back is shown", tool="done", result="pass", reason="Logged in"),
    ]})

    async with launcher.session(SITE, vault=VAULT) as session:
        result = await execute_test(llm, session, steps, tc, max_steps=10, timeout_seconds=60,
                                    credential_placeholders=VAULT.placeholders)
        typed_email = await session.page.locator("input[name=email]").input_value()

    assert typed_email == "demo@shop.test"  # the real value reached the page…
    assert result.final_snapshot is not None and "Welcome back" in result.final_snapshot.to_text()
    everything_sent = "\n".join(p for _, p, _ in llm.calls)
    assert "{{cred:Shopper:username}}" in everything_sent
    assert "demo@shop.test" not in everything_sent and "demo1234" not in everything_sent  # …but not the LLM
    saved = " ".join(f"{s.message} {s.action} {s.snapshot}" for s in await RunStep.find_all().to_list())
    assert "demo@shop.test" not in saved and "demo1234" not in saved and "{{cred:Shopper:password}}" in saved


async def test_executor_stops_at_max_steps_and_survives_tool_errors(
    launcher: FakeSiteLauncher, database: AsyncDatabase, storage  # noqa: ANN001
) -> None:
    steps, tc = await new_run_and_test(database)
    llm = FakeLLM({ExecutorDecision: [decide("Click a ref that does not exist", tool="click", ref=999),
                                      decide("Keep waiting", tool="wait", ms=0)]})

    async with launcher.session(SITE) as session:
        result = await execute_test(llm, session, steps, tc, max_steps=3, timeout_seconds=60, credential_placeholders=[])

    assert result.done is None and result.steps_used == 3 and result.stop_reason == "step limit of 3 reached"
    saved = await RunStep.find_all().sort(+RunStep.index).to_list()
    assert saved[2].kind == "observation" and "No element [999]" in saved[2].message
    assert saved[-1].message == "Executor stopped: step limit of 3 reached"
    assert "Failed: No element [999]" in llm.prompts(ExecutorDecision)[1]  # the model sees the failure


async def test_executor_fails_gracefully_on_invalid_model_output(
    launcher: FakeSiteLauncher, database: AsyncDatabase, storage  # noqa: ANN001
) -> None:
    steps, tc = await new_run_and_test(database)
    llm = FakeLLM({ExecutorDecision: [LLMOutputError("fake returned invalid JSON twice", raw="nonsense")]})

    async with launcher.session(SITE) as session:
        result = await execute_test(llm, session, steps, tc, max_steps=5, timeout_seconds=60, credential_placeholders=[])

    assert result.error and result.stop_reason == "invalid model output"
    assert (await RunStep.find_one(RunStep.kind == "error")) is not None


# ---------- judge ----------


async def test_judge_uses_final_screenshot_and_evidence() -> None:
    from worker.agents.executor import HistoryItem
    from worker.browser import Snapshot

    tc = TestCase.model_construct(index=0, title="Duplicate signup", type="edge", steps=["Sign up twice"],
                                  expected="The second signup is rejected")
    execution = ExecutionResult(
        done=Done(result="fail", reason="Second signup succeeded"),
        history=[HistoryItem(1, "Submit again", {"tool": "click", "ref": 7}, "Clicked [7]")],
        final_snapshot=Snapshot(url=SITE + "/signup", title="Sign up", elements=[]),
        final_screenshot=PNG_MAGIC + b"...",
        stop_reason="executor finished: fail",
    )
    llm = FakeLLM({Verdict: [Verdict(result="fail", reason="Duplicate email accepted")]})

    verdict = await judge_test(llm, tc, execution)

    assert verdict.result == "fail"
    schema, prompt, images = llm.calls[0]
    assert images == [PNG_MAGIC + b"..."]  # vision: the screenshot is attached
    for expected in ("The second signup is rejected", "Submit again → click(ref=7)", "Second signup succeeded",
                     "URL: http://shop.test/signup", "the REAL value was typed"):
        assert expected in prompt, expected


# ---------- orchestrator (whole run) ----------


@pytest.fixture
def storage(tmp_path, monkeypatch: pytest.MonkeyPatch):  # noqa: ANN001, ANN201
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "storage_dir", str(tmp_path))
    monkeypatch.setattr(get_settings(), "explore_max_pages", 6)
    return tmp_path


async def start_run(client: httpx.AsyncClient, headers: dict[str, str], base_url: str = SITE) -> tuple[str, str]:
    pid = (await client.post("/projects", json={"name": "Shop", "base_url": base_url, "authorised_testing_confirmed": True},
                             headers=headers)).json()["id"]
    await client.post(f"/projects/{pid}/credentials", json={"label": "Shopper", "username": "demo@shop.test",
                                                           "password": "demo1234"}, headers=headers)
    run = (await client.post(f"/projects/{pid}/runs", json={"goal": "test signup and login",
                                                           "options": {"max_tests": 2, "accessibility": False}}, headers=headers)).json()
    return pid, run["id"]


def launcher_factory(launcher: FakeSiteLauncher):  # noqa: ANN201
    @asynccontextmanager
    async def factory():  # noqa: ANN202
        yield launcher

    return factory


async def test_full_run_explores_plans_executes_and_judges(
    client: httpx.AsyncClient, register: RegisterFn, launcher: FakeSiteLauncher, storage  # noqa: ANN001
) -> None:
    headers = await register()
    _, run_id = await start_run(client, headers)
    plan = TestPlan.model_validate({"test_cases": [
        {"title": "Sign up with valid details", "type": "happy", "start_path": "/signup",
         "steps": ["Fill name, email, password", "Click Create account"], "expected": "Account created"},
        {"title": "Log in with the test account", "type": "happy", "start_path": "/login",
         "steps": ["Type the test login", "Click Log in"], "expected": "Welcome back"},
    ]})
    llm = FakeLLM({
        TestPlan: [plan],
        ExecutorDecision: [
            lambda p: decide("name", tool="type", ref=ref_for(p, "textbox", "Name"), text="Asha"),
            lambda p: decide("email", tool="type", ref=ref_for(p, "textbox", "Email"), text="not-an-email"),
            lambda p: decide("submit", tool="click", ref=ref_for(p, "button", "Create account")),
            lambda p: decide("It says Enter a valid email", tool="done", result="fail", reason="Validation error shown"),
            lambda p: decide("email", tool="type", ref=ref_for(p, "textbox", "Email"), text="{{cred:Shopper:username}}"),
            lambda p: decide("password", tool="type", ref=ref_for(p, "textbox", "Password"), text="{{cred:Shopper:password}}"),
            lambda p: decide("log in", tool="click", ref=ref_for(p, "button", "Log in")),
            lambda p: decide("Welcome back", tool="done", result="pass", reason="Logged in"),
        ],
        Verdict: [Verdict(result="blocked", reason="The agent typed an invalid email by mistake"),
                  Verdict(result="pass", reason="Welcome back shown")],
    })
    job = await worker.claim_next_job()
    assert job is not None

    await handle_run_job(job, launcher_factory=launcher_factory(launcher), llm=llm)

    run = (await client.get(f"/runs/{run_id}", headers=headers)).json()
    assert run["status"] == "completed", run["error"]
    assert run["stats"] | {"seconds": 0} == {"pages": 6, "tests": 2, "passed": 1, "failed": 0, "blocked": 1,
                                             "errors": 0, "bugs_new": 0, "bugs_repeated": 0, "tests_saved": 1,
                                             "llm_calls": 11, "input_tokens": run["stats"]["input_tokens"],
                                             "output_tokens": 0, "seconds": 0}
    cases = (await client.get(f"/runs/{run_id}/test-cases", headers=headers)).json()
    assert [(c["title"], c["status"]) for c in cases] == [("Sign up with valid details", "blocked"),
                                                          ("Log in with the test account", "passed")]
    assert cases[1]["reason"] == "Welcome back shown" and cases[1]["steps_used"] == 4
    steps = (await client.get(f"/runs/{run_id}/steps", headers=headers)).json()
    phases = [s["phase"] for s in steps]
    assert phases.index("explore") < phases.index("plan") < phases.index("execute") < phases.index("judge")
    assert any(s["message"].startswith("Verdict: PASS") for s in steps)
    assert launcher.sessions == 3  # explorer + a fresh browser context per test case
    # Judge got a screenshot for each test; no prompt contains the real credentials.
    assert all(images and images[0].startswith(PNG_MAGIC) for s, _, images in llm.calls if s is Verdict)
    assert not any("demo1234" in p or "demo@shop.test" in p for _, p, _ in llm.calls)
    assert '"Sign up" → /signup' in llm.prompts(TestPlan)[0]  # the planner saw the site map


async def test_run_fails_cleanly_when_the_llm_is_unavailable(
    client: httpx.AsyncClient, register: RegisterFn, launcher: FakeSiteLauncher, storage  # noqa: ANN001
) -> None:
    headers = await register()
    _, run_id = await start_run(client, headers)
    llm = FakeLLM({TestPlan: [LLMError("gemini: HTTP 429: quota exceeded")]})
    job = await worker.claim_next_job()
    assert job is not None

    await worker.run_job(job, {"run": lambda j: handle_run_job(j, launcher_factory=launcher_factory(launcher), llm=llm)})

    run = (await client.get(f"/runs/{run_id}", headers=headers)).json()
    assert run["status"] == "failed" and run["error"] == "gemini: HTTP 429: quota exceeded"
    assert run["stats"]["pages"] == 6 and run["stats"]["tests"] == 0


async def test_run_fails_when_site_cannot_be_opened(
    client: httpx.AsyncClient, register: RegisterFn, launcher: FakeSiteLauncher, storage  # noqa: ANN001
) -> None:
    headers = await register()
    _, run_id = await start_run(client, headers, base_url="http://localhost:3000")
    job = await worker.claim_next_job()
    assert job is not None

    class DeadSite(FakeSiteLauncher):
        @asynccontextmanager
        async def session(self, base_url: str, **_: Any) -> AsyncIterator[BrowserSession]:  # noqa: ANN401
            context = await self.browser.new_context()
            await context.route("**/*", lambda route: route.abort("connectionrefused"))
            try:
                yield BrowserSession(await context.new_page(), allowed_origin="http://localhost:3000", action_delay_ms=0,
                                     timeout_ms=5000)
            finally:
                await context.close()

    with pytest.raises(RuntimeError):
        await handle_run_job(job, launcher_factory=launcher_factory(DeadSite(launcher.browser)), llm=FakeLLM({}))

    run = (await client.get(f"/runs/{run_id}", headers=headers)).json()
    assert run["status"] == "failed" and "Could not open the site" in run["error"]
    assert "host.docker.internal" in run["error"]


async def test_executor_batches_actions_and_stops_batch_on_navigation(
    launcher: FakeSiteLauncher, database: AsyncDatabase, storage  # noqa: ANN001
) -> None:
    steps, tc = await new_run_and_test(database)

    def fill_and_submit(p: str) -> dict:
        return decide(
            "Fill the whole form and submit it",
            {"tool": "type", "ref": ref_for(p, "textbox", "Name"), "text": "Asha"},
            {"tool": "type", "ref": ref_for(p, "textbox", "Email"), "text": "asha@example.com"},
            {"tool": "type", "ref": ref_for(p, "textbox", "Password"), "text": "Sup3r-Secret!"},
            {"tool": "click", "ref": ref_for(p, "button", "Create account")},
            {"tool": "done", "result": "pass", "reason": "too early: result not seen yet"},
        )

    def go_home_then_more(p: str) -> dict:
        return decide("Go home, then (wrongly) keep using old refs",
                      {"tool": "click", "ref": ref_for(p, "link", "Home")},
                      {"tool": "click", "ref": ref_for(p, "button", "Create account")})

    llm = FakeLLM({ExecutorDecision: [
        fill_and_submit,
        go_home_then_more,
        lambda p: decide("Done", tool="done", result="pass", reason="Account created was shown"),
    ]})

    async with launcher.session(SITE) as session:
        result = await execute_test(llm, session, steps, tc, max_steps=20, timeout_seconds=60, credential_placeholders=[])

    assert len(llm.calls) == 3  # 4 form actions in ONE call; done held back until the result was seen
    assert result.done is not None and result.done.reason == "Account created was shown"
    lines = [h.to_line() for h in result.history]
    assert len(lines) == 5 and "click(ref=" in lines[4]  # the batch after navigation stopped at the link click
    assert "Account created" in llm.prompts(ExecutorDecision)[1]  # it saw the outcome of the submit
    assert result.steps_used == 6
