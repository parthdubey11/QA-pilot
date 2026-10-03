"""Runs a test run: Explorer → Planner → (Executor → Judge) per test case.

Every thought/action/observation is saved as a run_steps document; the API streams them to the dashboard.
One browser per run, a fresh browser context (cookies, cart…) per test case.
"""

import logging
import time
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Protocol
from urllib.parse import urlsplit

from beanie import PydanticObjectId

from app.core.config import get_settings
from app.core.security import decrypt_secret
from app.models.accessibility import A11yAudit, A11yIssue, A11yPageScore
from app.models.base import utcnow
from app.models.job import Job
from app.models.project import Credential, Project
from app.models.run import Run
from app.models.test_case import TestCase
from app.services.run_steps import StepRecorder, storage_root
from worker.agents.executor import ExecutionResult, execute_test
from worker.agents.explorer import SiteMap, SitePage, explore
from worker.agents.judge import judge_test
from worker.agents.planner import plan_tests
from worker.agents.reporter import report_bug
from worker.browser import BrowserLauncher, BrowserSession, SecretVault, Snapshot, TestCredential
from worker.audits.accessibility import audit_page, page_template
from worker.recorder import save_passing_test
from worker.browser.tools import ToolError
from worker.llm import FallbackProvider, LLMError, LLMOutputError, LLMProvider, get_provider

logger = logging.getLogger("qa_pilot.orchestrator")

VERDICT_STATUS = {"pass": "passed", "fail": "failed", "blocked": "blocked"}


class Launcher(Protocol):
    def session(self, base_url: str, *, mobile: bool = ..., action_delay_ms: int = ..., timeout_ms: int = ...,
                vault: SecretVault | None = ...) -> AbstractAsyncContextManager[BrowserSession]: ...


LauncherFactory = Callable[[], AbstractAsyncContextManager[Launcher]]


def default_launcher() -> AbstractAsyncContextManager[Launcher]:
    settings = get_settings()
    return BrowserLauncher.start(headless=settings.browser_headless, channel=settings.browser_channel or None)


def unreachable_hint(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    if host in {"localhost", "127.0.0.1", "::1"}:
        return (" The worker runs inside Docker, where localhost is the worker itself. "
                "For the demo shop use http://demo-shop:8000; for a site on your PC use http://host.docker.internal:PORT.")
    return ""


async def load_vault(project: Project) -> SecretVault:
    credentials = await Credential.find(Credential.project_id == project.id).sort(+Credential.created_at).to_list()
    return SecretVault([TestCredential(c.label, c.username, decrypt_secret(c.password_encrypted)) for c in credentials])


async def run_accessibility_audit(
    llm: LLMProvider,
    session: BrowserSession,
    steps: StepRecorder,
    run: Run,
    project: Project,
    site_map: SiteMap,
    stats: dict,
    *,
    redact: Callable[[str], str],
) -> None:
    """axe-core + keyboard + alt-text audit per page template. If the LLM becomes unavailable (quota), the rest
    of the audit continues without it instead of failing the run."""
    settings = get_settings()
    assert run.id is not None and project.id is not None
    by_template: dict[str, list[str]] = {}
    for page in site_map.pages:
        if not page.error:
            by_template.setdefault(page_template(page.path), []).append(page.path)
    templates = list(by_template.items())[: settings.a11y_max_pages]
    await steps.add("info", f"Accessibility audit of {len(templates)} page(s): axe-core WCAG rules, keyboard-only "
                    "navigation and alt-text quality", phase="audit")

    audit_llm: LLMProvider | None = llm
    seen_images: set[str] = set()
    page_scores: list[A11yPageScore] = []
    issue_count = 0
    for _, paths in templates:
        path = paths[0]
        try:
            await session.goto(path)
        except ToolError as exc:
            page_scores.append(A11yPageScore(path=path, url=path, title="", score=0, issues=0, same_as=paths[1:],
                                             error=str(exc)))
            continue
        result = await audit_page(session.page, audit_llm, seen_image_srcs=seen_images, max_tabs=settings.a11y_max_tabs)
        if result.llm_unavailable and audit_llm is not None:
            audit_llm = None
            await steps.add("observation", f"LLM checks stopped for the rest of the audit: {result.llm_unavailable}",
                            phase="audit")
        for finding in result.findings:
            await A11yIssue(project_id=project.id, run_id=run.id, page_path=path, page_url=result.url,
                            **finding.model_dump() | {"description": redact(finding.description)}).insert()
        issue_count += len(result.findings)
        page_scores.append(A11yPageScore(path=path, url=result.url, title=result.title, score=result.score,
                                         issues=len(result.findings), same_as=paths[1:]))
        absolute, relative = steps.screenshot_path()
        await session.screenshot(absolute)
        summary = ", ".join(sorted({f.rule_id for f in result.findings})) or "no issues"
        await steps.add("observation", f"Accessibility of {path}: {result.score}/100 — {summary}"
                        + (f" ({'; '.join(result.notes)})" if result.notes else ""),
                        phase="audit", url=result.url, screenshot_path=relative)

    audited = [p for p in page_scores if p.error is None]
    score = round(sum(p.score for p in audited) / len(audited)) if audited else 0
    await A11yAudit(project_id=project.id, run_id=run.id, score=score, pages=page_scores, issue_count=issue_count,
                    llm_checks=audit_llm is not None).insert()
    stats["a11y_score"], stats["a11y_issues"] = score, issue_count
    await steps.add("info", f"Accessibility score: {score}/100 across {len(audited)} page(s), {issue_count} issue(s)",
                    phase="audit")


async def report_failure(
    llm: LLMProvider,
    steps: StepRecorder,
    run: Run,
    project: Project,
    tc: TestCase,
    reason: str,
    execution: ExecutionResult,
    stats: dict,
    *,
    redact: Callable[[str], str],
) -> None:
    """Reporter agent: file (or merge) a bug for a failed test. A bad model reply doesn't stop the run."""
    assert run.id is not None and project.id is not None
    try:
        outcome = await report_bug(llm, project_id=project.id, run_id=run.id, test=tc, verdict_reason=reason,
                                   execution=execution, screenshot_path=tc.final_screenshot_path, redact=redact)
    except LLMOutputError as exc:
        await steps.add("error", f"Could not write a bug report: {exc}", phase="report", test_case_index=tc.index)
        return
    bug = outcome.bug
    tc.bug_id = bug.id
    if outcome.created:
        stats["bugs_new"] += 1
        message = f"New bug ({bug.severity}): {bug.title}"
    else:
        stats["bugs_repeated"] += 1
        message = (f"Seen again ({bug.occurrences}×){' — reopened, it was marked fixed' if outcome.reopened else ''}: "
                   f"{bug.title}")
    await steps.add("info", message, phase="report", test_case_index=tc.index, action={"bug_id": str(bug.id)})


async def handle_run_job(
    job: Job,
    *,
    launcher_factory: LauncherFactory = default_launcher,
    llm: LLMProvider | None = None,
) -> None:
    settings = get_settings()
    run = await Run.get(PydanticObjectId(job.payload["run_id"]))
    if run is None:
        raise LookupError(f"Run {job.payload['run_id']} no longer exists")
    project = await Project.get(run.project_id)
    if project is None:
        raise LookupError(f"Project {run.project_id} no longer exists")
    assert run.id is not None and project.id is not None

    if run.kind == "replay":
        from worker.replayer import handle_replay_run  # noqa: PLC0415 - avoids an import cycle

        await handle_replay_run(job, run, project, launcher_factory=launcher_factory, llm=llm,
                                vault=await load_vault(project))
        return

    started = time.monotonic()
    run.status, run.started_at, run.error = "running", utcnow(), None
    await run.save()
    steps = await StepRecorder.for_run(run)
    llm = llm or get_provider()
    if isinstance(llm, FallbackProvider):
        async def note_switch(message: str) -> None:
            await steps.add("info", message)
        llm.on_switch = note_switch
    vault = await load_vault(project)
    session_options = {
        "mobile": run.options.mobile_viewport,
        "action_delay_ms": settings.action_delay_ms,
        "timeout_ms": settings.navigation_timeout_ms,
        "vault": vault,
    }
    stats: dict = {"pages": 0, "tests": 0, "passed": 0, "failed": 0, "blocked": 0, "errors": 0,
                   "bugs_new": 0, "bugs_repeated": 0, "tests_saved": 0}
    test_cases: list[TestCase] = []

    try:
        await steps.add("info", f"Starting run: {run.goal}")
        async with launcher_factory() as launcher:
            # 1. Explore
            await steps.add("info", f"Exploring {project.base_url} (up to {settings.explore_max_pages} pages)",
                            phase="explore")

            async def on_page(page: SitePage, snapshot: Snapshot | None) -> None:
                if page.error:
                    await steps.add("observation", f"Could not open {page.path}: {page.error}", phase="explore")
                    return
                assert snapshot is not None
                await steps.add("observation", f'Explored {page.path} — "{page.title}": {len(page.forms)} form(s), '
                                f"{len(page.links)} link(s)", phase="explore", url=snapshot.url,
                                snapshot=vault.redact(snapshot.to_text()))

            async with launcher.session(project.base_url, **session_options) as session:
                site_map = await explore(session, project.base_url, max_pages=settings.explore_max_pages,
                                         max_depth=settings.explore_max_depth, on_page=on_page)
            stats["pages"] = len([p for p in site_map.pages if not p.error])
            if stats["pages"] == 0:
                first_error = next((p.error for p in site_map.pages if p.error), "no pages found")
                raise RuntimeError(f"Could not open the site: {first_error}")

            # 1b. Accessibility audit of the explored pages (one page per template)
            if run.options.accessibility:
                async with launcher.session(project.base_url, **session_options) as session:
                    await run_accessibility_audit(llm, session, steps, run, project, site_map, stats,
                                                  redact=vault.redact)

            # 2. Plan
            await steps.add("info", "Planning test cases from your goal and the site map", phase="plan")
            planned = await plan_tests(
                llm,
                goal=run.goal,
                site_map_text=vault.redact(site_map.to_text()),
                max_tests=run.options.max_tests,
                credential_placeholders=vault.placeholders,
                security_probes=project.is_own_site and project.security_probes_enabled,
            )
            for i, p in enumerate(planned):
                tc = TestCase(run_id=run.id, project_id=project.id, index=i, title=p.title, type=p.type,
                              start_path=p.start_path, steps=p.steps, expected=p.expected)
                await tc.insert()
                test_cases.append(tc)
            stats["tests"] = len(test_cases)
            await steps.add("info", f"Planned {len(test_cases)} test cases:\n"
                            + "\n".join(f"{t.index + 1}. [{t.type}] {t.title}" for t in test_cases), phase="plan")

            # 3 + 4. Execute and judge each test case
            for tc in test_cases:
                tc.status, tc.started_at, tc.updated_at = "running", utcnow(), utcnow()
                await tc.save()
                await steps.add("info", f"Test {tc.index + 1}/{len(test_cases)}: {tc.title}",
                                phase="execute", test_case_index=tc.index)
                async with launcher.session(project.base_url, **session_options) as session:
                    execution = await execute_test(
                        llm, session, steps, tc,
                        max_steps=settings.max_steps_per_test,
                        timeout_seconds=settings.test_timeout_seconds,
                        credential_placeholders=vault.placeholders,
                    )
                tc.steps_used = execution.steps_used
                if execution.final_snapshot:
                    tc.final_url = execution.final_snapshot.url
                if execution.final_screenshot:
                    relative = f"runs/{run.id}/test-{tc.index + 1:02d}-final.png"
                    path = storage_root() / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(execution.final_screenshot)
                    tc.final_screenshot_path = relative
                if execution.error:
                    tc.status, tc.reason = "error", f"The test could not be run: {execution.error}"
                else:
                    verdict = await judge_test(llm, tc, execution, redact=vault.redact)
                    tc.status, tc.reason = VERDICT_STATUS[verdict.result], verdict.reason  # type: ignore[assignment]
                    await steps.add("thought", f"Verdict: {verdict.result.upper()} — {verdict.reason}",
                                    phase="judge", test_case_index=tc.index)
                    if verdict.result == "pass":
                        saved = await save_passing_test(project_id=project.id, run_id=run.id, test=tc,
                                                        execution=execution, redact=vault.redact)
                        if saved is not None:
                            stats["tests_saved"] += 1
                            await steps.add("info", f"Saved as a replayable test ({len(saved.steps)} steps, "
                                            f"{len(saved.assertions)} checks)", phase="judge", test_case_index=tc.index)
                    if verdict.result == "fail":
                        await report_failure(llm, steps, run, project, tc, verdict.reason, execution, stats,
                                             redact=vault.redact)
                tc.finished_at = tc.updated_at = utcnow()
                await tc.save()

        run.status = "completed"
        await steps.add("info", "Run finished")
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}" if not isinstance(exc, (LLMError, RuntimeError)) else str(exc)
        message = message.strip().splitlines()[0] + unreachable_hint(project.base_url)
        logger.warning("run %s failed: %s", run.id, message)
        for tc in test_cases:
            if tc.status in ("pending", "running"):
                tc.status, tc.reason = "error", f"Not finished: {message}"
            tc.updated_at = utcnow()
            await tc.save()  # also persists a verdict reached just before the failure
        await steps.add("error", message)
        run.status, run.error = "failed", message
        raise
    finally:
        for tc in test_cases:
            key = {"passed": "passed", "failed": "failed", "blocked": "blocked", "error": "errors"}.get(tc.status)
            if key:
                stats[key] += 1
        if isinstance(llm, FallbackProvider) and len(llm.used) > 1:
            stats["llm_providers"] = llm.used
        stats |= {"llm_calls": llm.usage.calls, "input_tokens": llm.usage.input_tokens,
                  "output_tokens": llm.usage.output_tokens, "seconds": round(time.monotonic() - started, 1)}
        run.stats, run.finished_at = stats, utcnow()
        await run.save()
