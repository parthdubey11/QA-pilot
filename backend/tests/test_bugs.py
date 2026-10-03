"""Reporter agent (bug reports + dedup), bug API, and the downloadable HTML report. LLM always mocked."""

from pathlib import Path

import httpx
import pytest
from beanie import PydanticObjectId
from pymongo.asynchronous.database import AsyncDatabase

from app.models.base import utcnow
from app.models.bug import Bug, title_key
from app.models.run import Run, RunStep
from app.models.test_case import TestCase
from tests.conftest import RegisterFn
from tests.test_agents import (  # noqa: F401 - fixtures
    PNG_MAGIC,
    VAULT,
    FakeLLM,
    FakeSiteLauncher,
    browser,
    decide,
    launcher,
    launcher_factory,
    ref_for,
    start_run,
    storage,
)
from worker import main as worker
from worker.agents.executor import ExecutionResult, ExecutorDecision, HistoryItem
from worker.agents.judge import Verdict
from worker.agents.planner import TestPlan
from worker.agents.reporter import BugReport, report_bug
from worker.browser import Snapshot
from worker.browser.tools import Done
from worker.orchestrator import handle_run_job

PROJECT = PydanticObjectId()


def report(**overrides: object) -> BugReport:
    return BugReport.model_validate({
        "title": "Signup accepts duplicate email addresses",
        "severity": "high",
        "steps": ["Open /signup", "Sign up as qa@example.com twice"],
        "expected": "The second signup is rejected",
        "actual": "Account created. You can now log in.",
        "suggested_fix": "Add a unique check on email in the signup handler",
        "duplicate_of": None,
    } | overrides)


def execution(url: str = "http://shop.test/signup") -> ExecutionResult:
    return ExecutionResult(
        done=Done(result="fail", reason="accepted"),
        history=[HistoryItem(1, "Type the password", {"tool": "type", "ref": 3, "text": "{{cred:Shopper:password}}"},
                             "Typed into [3]")],
        final_snapshot=Snapshot(url=url, title="Sign up", elements=[]),
        final_screenshot=PNG_MAGIC,
        stop_reason="executor finished: fail",
    )


def test_case() -> TestCase:
    return TestCase.model_construct(index=0, title="Duplicate signup", type="edge",
                                    steps=["Sign up twice with the same email"], expected="Second signup rejected")


async def file_bug(llm: FakeLLM, run_id: PydanticObjectId | None = None, shot: str | None = "runs/x/final.png"):  # noqa: ANN201
    return await report_bug(llm, project_id=PROJECT, run_id=run_id or PydanticObjectId(), test=test_case(),
                            verdict_reason="Second signup succeeded", execution=execution(),
                            screenshot_path=shot, redact=VAULT.redact)


# ---------- reporter ----------


async def test_reporter_creates_bug_from_failed_test(database: AsyncDatabase) -> None:
    llm = FakeLLM({BugReport: [report(steps=["Log in with demo@shop.test / demo1234", "Sign up again"])]})
    run_id = PydanticObjectId()

    outcome = await file_bug(llm, run_id)

    bug = outcome.bug
    assert outcome.created and await Bug.count() == 1
    assert (bug.title, bug.severity, bug.status, bug.occurrences) == ("Signup accepts duplicate email addresses",
                                                                      "high", "open", 1)
    assert bug.run_ids == [run_id] and bug.first_run_id == run_id and bug.first_test_title == "Duplicate signup"
    assert bug.url == "http://shop.test/signup" and bug.screenshot_path == "runs/x/final.png"
    assert bug.steps[0] == "Log in with {{cred:Shopper:username}} / {{cred:Shopper:password}}"  # never stored
    prompt = llm.prompts(BugReport)[0]
    for expected in ("Duplicate signup", "Second signup succeeded", "{{cred:Shopper:password}}", "(none yet)",
                     "critical", "suggested_fix", "duplicate_of"):
        assert expected in prompt, expected


async def test_duplicate_is_merged_into_existing_bug(database: AsyncDatabase) -> None:
    first_run, second_run = PydanticObjectId(), PydanticObjectId()
    await file_bug(FakeLLM({BugReport: [report()]}), first_run)
    llm = FakeLLM({BugReport: [report(title="Same email can register twice", duplicate_of=1)]})

    outcome = await file_bug(llm, second_run, shot="runs/y/final.png")

    assert not outcome.created and await Bug.count() == 1
    assert outcome.bug.occurrences == 2 and outcome.bug.run_ids == [first_run, second_run]
    assert outcome.bug.title == "Signup accepts duplicate email addresses"  # original report kept
    assert outcome.bug.screenshot_path == "runs/y/final.png"  # latest evidence
    assert "[1] (open, high) Signup accepts duplicate email addresses" in llm.prompts(BugReport)[0]


async def test_exact_title_repeat_is_merged_even_if_model_misses_it(database: AsyncDatabase) -> None:
    await file_bug(FakeLLM({BugReport: [report()]}))
    outcome = await file_bug(FakeLLM({BugReport: [report(title="signup ACCEPTS duplicate email-addresses!")]}))
    assert not outcome.created and await Bug.count() == 1


async def test_invalid_duplicate_index_creates_new_bug(database: AsyncDatabase) -> None:
    outcome = await file_bug(FakeLLM({BugReport: [report(duplicate_of=7)]}))
    assert outcome.created


async def test_fixed_bug_seen_again_is_reopened_but_ignored_stays_ignored(database: AsyncDatabase) -> None:
    fixed = (await file_bug(FakeLLM({BugReport: [report()]}))).bug
    fixed.status = "fixed"
    await fixed.save()
    ignored = (await file_bug(FakeLLM({BugReport: [report(title="Cart badge does not update")]}))).bug
    ignored.status = "ignored"
    await ignored.save()

    again = await file_bug(FakeLLM({BugReport: [report(duplicate_of=None)]}))
    still = await file_bug(FakeLLM({BugReport: [report(title="Cart badge does not update")]}))

    assert again.reopened and again.bug.status == "open" and again.bug.reopened
    assert not still.reopened and still.bug.status == "ignored" and still.bug.occurrences == 2


# ---------- whole run: failed tests become bugs, deduplicated across runs ----------


async def run_once(client: httpx.AsyncClient, headers: dict[str, str], launcher: FakeSiteLauncher,
                   duplicate_of: int | None, project_id: str | None = None) -> dict:
    if project_id is None:
        project_id, _ = await start_run(client, headers)
    else:
        res = await client.post(f"/projects/{project_id}/runs", json={"goal": "test signup", "options": {"max_tests": 2, "accessibility": False}},
                                headers=headers)
        assert res.status_code == 201, res.text
    plan = TestPlan.model_validate({"test_cases": [
        {"title": "Reject invalid email", "type": "edge", "start_path": "/signup",
         "steps": ["Type 'bad' as email", "Submit"], "expected": "An error is shown"},
        {"title": "Cart page opens", "type": "happy", "start_path": "/cart", "steps": ["Open cart"],
         "expected": "Cart shown"},
    ]})
    llm = FakeLLM({
        TestPlan: [plan],
        ExecutorDecision: [
            lambda p: decide("submit", {"tool": "type", "ref": ref_for(p, "textbox", "Email"), "text": "bad"},
                             {"tool": "click", "ref": ref_for(p, "button", "Create account")}),
            lambda p: decide("error shown?", tool="done", result="fail", reason="No error"),
            lambda p: decide("cart shown", tool="done", result="pass", reason="Cart"),
        ],
        Verdict: [Verdict(result="fail", reason="No error for a malformed email"), Verdict(result="pass", reason="ok")],
        BugReport: [report(title="Signup shows no error for malformed email", severity="medium",
                           duplicate_of=duplicate_of)],
    })
    job = await worker.claim_next_job()
    assert job is not None
    await handle_run_job(job, launcher_factory=launcher_factory(launcher), llm=llm)
    run = await Run.get(PydanticObjectId(job.payload["run_id"]))
    assert run is not None and run.status == "completed", run.error if run else None
    return {"run": run, "llm": llm, "project_id": project_id}


async def test_run_reports_bugs_for_failed_tests_and_dedups_next_run(
    client: httpx.AsyncClient, register: RegisterFn, launcher: FakeSiteLauncher, storage: Path
) -> None:
    headers = await register()
    first = await run_once(client, headers, launcher, duplicate_of=None)

    assert first["run"].stats["bugs_new"] == 1 and first["run"].stats["bugs_repeated"] == 0
    bugs = (await client.get("/bugs", headers=headers)).json()
    assert [(b["title"], b["severity"], b["occurrences"]) for b in bugs] == [
        ("Signup shows no error for malformed email", "medium", 1)]
    cases = (await client.get(f"/runs/{first['run'].id}/test-cases", headers=headers)).json()
    assert cases[0]["bug_id"] == bugs[0]["id"] and cases[1]["bug_id"] is None  # only the failed test
    assert cases[0]["has_final_screenshot"] and cases[1]["has_final_screenshot"]
    shot = await client.get(f"/runs/{first['run'].id}/test-cases/0/screenshot", headers=headers)
    assert shot.status_code == 200 and shot.content.startswith(PNG_MAGIC)
    assert (await client.get(f"/bugs/{bugs[0]['id']}/screenshot", headers=headers)).content.startswith(PNG_MAGIC)
    steps = (await client.get(f"/runs/{first['run'].id}/steps", headers=headers)).json()
    assert any(s["phase"] == "report" and s["message"].startswith("New bug (medium)") for s in steps)
    assert len(first["llm"].prompts(BugReport)) == 1  # reporter only runs for failures

    # Run again on the same project: same bug, merged instead of duplicated.
    second = await run_once(client, headers, launcher, duplicate_of=1, project_id=first["project_id"])
    assert second["run"].stats["bugs_new"] == 0 and second["run"].stats["bugs_repeated"] == 1
    bugs = (await client.get("/bugs", headers=headers)).json()
    assert len(bugs) == 1 and bugs[0]["occurrences"] == 2 and len(bugs[0]["run_ids"]) == 2
    steps = (await client.get(f"/runs/{second['run'].id}/steps", headers=headers)).json()
    assert any(s["message"].startswith("Seen again (2×)") for s in steps)


# ---------- bug API ----------


async def make_bug(project_id: str, title: str, severity: str = "medium", status: str = "open",
                   run_id: PydanticObjectId | None = None, **fields: object) -> Bug:
    run_id = run_id or PydanticObjectId()
    bug = Bug(project_id=PydanticObjectId(project_id), title=title, title_key=title_key(title), severity=severity,
              status=status, steps=["Open /"], expected="works", actual="broken", suggested_fix="fix it",
              run_ids=[run_id], first_run_id=run_id, first_test_title="t", **fields)
    await bug.insert()
    return bug


async def new_project(client: httpx.AsyncClient, headers: dict[str, str], name: str = "Shop") -> str:
    res = await client.post("/projects", json={"name": name, "base_url": "http://shop.test",
                                               "authorised_testing_confirmed": True}, headers=headers)
    return res.json()["id"]


async def test_bug_list_filters_and_sorting(client: httpx.AsyncClient, register: RegisterFn) -> None:
    headers = await register()
    shop, blog = await new_project(client, headers, "Shop"), await new_project(client, headers, "Blog")
    run_id = PydanticObjectId()
    await make_bug(shop, "Low one", "low")
    await make_bug(shop, "Critical one", "critical", run_id=run_id)
    await make_bug(shop, "Fixed high", "high", status="fixed")
    await make_bug(blog, "Blog medium", "medium", status="ignored")

    async def titles(query: str = "") -> list[str]:
        return [b["title"] for b in (await client.get(f"/bugs{query}", headers=headers)).json()]

    assert await titles() == ["Critical one", "Fixed high", "Blog medium", "Low one"]  # most severe first
    assert await titles("?status=open") == ["Critical one", "Low one"]
    assert await titles("?severity=critical&severity=high") == ["Critical one", "Fixed high"]
    assert await titles(f"?project_id={blog}") == ["Blog medium"]
    assert await titles(f"?run_id={run_id}") == ["Critical one"]
    listed = (await client.get("/bugs", headers=headers)).json()[0]
    assert listed["project_name"] == "Shop" and "title_key" not in listed and "screenshot_path" not in listed
    assert (await client.get("/bugs?severity=urgent", headers=headers)).status_code == 422


async def test_bugs_are_private_and_status_can_be_changed(client: httpx.AsyncClient, register: RegisterFn) -> None:
    alice = await register(email="alice@example.com")
    bob = await register(email="bob@example.com")
    shop = await new_project(client, alice)
    bug = await make_bug(shop, "Cart total wrong", "high", reopened=True)

    assert (await client.get("/bugs", headers=bob)).json() == []
    for res in (await client.get(f"/bugs/{bug.id}", headers=bob),
                await client.patch(f"/bugs/{bug.id}", json={"status": "fixed"}, headers=bob),
                await client.get(f"/bugs?project_id={shop}", headers=bob),
                await client.get("/bugs/not-an-id", headers=alice)):
        assert res.status_code == 404
    assert (await client.get("/bugs")).status_code == 401

    fixed = await client.patch(f"/bugs/{bug.id}", json={"status": "fixed"}, headers=alice)
    assert fixed.status_code == 200 and fixed.json()["status"] == "fixed" and fixed.json()["reopened"] is False
    assert (await client.patch(f"/bugs/{bug.id}", json={"status": "deleted"}, headers=alice)).status_code == 422
    assert (await client.get(f"/bugs/{bug.id}/screenshot", headers=alice)).status_code == 404  # none stored

    assert (await client.delete(f"/projects/{shop}", headers=alice)).status_code == 204
    assert await Bug.count() == 0


async def test_html_report_is_self_contained_and_escaped(
    client: httpx.AsyncClient, register: RegisterFn, storage: Path
) -> None:
    headers = await register()
    pid, run_id = await start_run(client, headers)
    run = await Run.get(PydanticObjectId(run_id))
    assert run is not None and run.id is not None
    run.status, run.started_at, run.finished_at = "completed", utcnow(), utcnow()
    run.stats = {"pages": 5, "llm_calls": 12, "input_tokens": 9000, "output_tokens": 500, "seconds": 95}
    await run.save()
    shot = storage / "runs" / run_id / "test-01-final.png"
    shot.parent.mkdir(parents=True)
    shot.write_bytes(PNG_MAGIC + b"-final")
    bug = await make_bug(pid, "Signup <script>alert(1)</script> accepted", "high", run_id=run.id,
                         screenshot_path=f"runs/{run_id}/test-01-final.png")
    await TestCase(run_id=run.id, project_id=run.project_id, index=0, title="Duplicate signup", type="edge",
                   steps=["Sign up twice"], expected="Rejected", status="failed", reason="Accepted twice",
                   final_screenshot_path=f"runs/{run_id}/test-01-final.png", bug_id=bug.id).insert()
    await TestCase(run_id=run.id, project_id=run.project_id, index=1, title="Login works", type="happy",
                   steps=["Log in"], expected="Welcome", status="passed", reason="ok").insert()
    await RunStep(run_id=run.id, index=0, kind="thought", message="Typing the <email>", phase="execute",
                  test_case_index=0).insert()

    res = await client.get(f"/runs/{run_id}/report.html", headers=headers)

    assert res.status_code == 200 and res.headers["content-type"].startswith("text/html")
    assert res.headers["content-disposition"].startswith('attachment; filename="qa-pilot-report-Shop-')
    html = res.text
    assert "<script>" not in html and "Signup &lt;script&gt;alert(1)&lt;/script&gt; accepted" in html
    assert "Typing the &lt;email&gt;" in html and "Agent log (1 steps)" in html
    assert html.count("data:image/png;base64,") == 2  # bug + failed test's final screen, embedded
    assert "http://" not in html.replace("http://shop.test", "")  # no external resources
    for text in ("Duplicate signup", "Login works", "Passed", "Failed", "test signup and login", "12 calls, 9,500 tokens",
                 "1 min 35 s", "Suggested fix", "Save as PDF"):
        assert text in html, text
    assert (await client.get(f"/runs/{run_id}/report.html")).status_code == 401


async def test_verdict_is_kept_when_reporter_hits_quota(
    client: httpx.AsyncClient, register: RegisterFn, launcher: FakeSiteLauncher, storage: Path
) -> None:
    from worker.llm import LLMQuotaError

    headers = await register()
    await start_run(client, headers)
    plan = TestPlan.model_validate({"test_cases": [
        {"title": "Reject invalid email", "type": "edge", "start_path": "/signup", "steps": ["Submit"], "expected": "Error"},
        {"title": "Cart page opens", "type": "happy", "start_path": "/cart", "steps": ["Open"], "expected": "Cart"},
    ]})
    llm = FakeLLM({
        TestPlan: [plan],
        ExecutorDecision: [decide("no error shown", tool="done", result="fail", reason="No error")],
        Verdict: [Verdict(result="fail", reason="No error for a malformed email")],
        BugReport: [LLMQuotaError("groq: the daily request quota for qwen is used up")],
    })
    job = await worker.claim_next_job()
    assert job is not None

    await worker.run_job(job, {"run": lambda j: handle_run_job(j, launcher_factory=launcher_factory(launcher), llm=llm)})

    run = await Run.get(PydanticObjectId(job.payload["run_id"]))
    assert run is not None and run.status == "failed" and "daily request quota" in (run.error or "")
    cases = await TestCase.find(TestCase.run_id == run.id).sort(+TestCase.index).to_list()
    assert [(c.status, c.reason) for c in cases] == [
        ("failed", "No error for a malformed email"),  # verdict saved although the reporter failed
        ("error", "Not finished: groq: the daily request quota for qwen is used up"),
    ]
    assert run.stats["failed"] == 1 and run.stats["errors"] == 1
