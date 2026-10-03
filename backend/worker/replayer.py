"""Replay saved tests without the LLM. When a step's element is gone, fall back to other locators and, if they
all fail, ask the Healer agent; every repair is written to the saved test's heal history."""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from beanie import PydanticObjectId
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Locator

from app.core.config import get_settings
from app.models.base import utcnow
from app.models.job import Job
from app.models.project import Project
from app.models.run import Run
from app.models.saved_test import HealEvent, Notification, SavedStep, SavedTest
from app.models.test_case import TestCase
from app.services.run_steps import StepRecorder, storage_root
from worker.agents.healer import find_replacement
from worker.browser import BrowserSession, SecretVault, ToolError
from worker.browser.locators import locators_for, resolve
from worker.browser.snapshot import REF_ATTRIBUTE
from worker.llm import FallbackProvider, LLMError, LLMProvider, get_provider

if TYPE_CHECKING:
    from worker.orchestrator import LauncherFactory

logger = logging.getLogger("qa_pilot.replayer")

MAX_HEAL_HISTORY = 50
ASSERT_TIMEOUT_MS = 5000


class StepFailed(Exception):
    pass


@dataclass
class ReplayOutcome:
    passed: bool
    reason: str
    heals: list[HealEvent] = field(default_factory=list)
    steps_done: int = 0


def assertion_text(saved: SavedTest) -> str:
    parts = [f'"{a.value}" is shown' if a.kind == "text" else f"ends on {a.value}" for a in saved.assertions]
    return "; ".join(parts) or "all steps succeed"


async def _act(session: BrowserSession, element: Locator, step: SavedStep) -> None:
    timeout = session.timeout_ms
    if step.tool == "click":
        await element.click(timeout=timeout)
    elif step.tool == "type":
        try:
            text = session.vault.fill(step.text or "")
        except KeyError as exc:
            raise StepFailed(str(exc.args[0])) from None
        await element.fill(text, timeout=timeout)
    elif step.tool == "select":
        try:
            await element.select_option(label=step.value, timeout=timeout)
        except PlaywrightError:
            await element.select_option(value=step.value, timeout=timeout)
    await session.settle()


async def replay_test(
    session: BrowserSession,
    saved: SavedTest,
    tc: TestCase,
    steps: StepRecorder,
    run_id: PydanticObjectId,
    get_llm: Callable[[], LLMProvider | None],
) -> ReplayOutcome:
    """Replay one saved test. Heals update `saved.steps` in place (the caller saves the document)."""
    tag = {"phase": "replay", "test_case_index": tc.index}
    outcome = ReplayOutcome(passed=False, reason="")
    done: list[str] = []

    async def record(message: str, kind: str = "action") -> None:
        absolute, relative = steps.screenshot_path()
        await session.screenshot(absolute)
        await steps.add(kind, session.redact(message), url=session.url, screenshot_path=relative, **tag)  # type: ignore[arg-type]

    try:
        await record((await session.goto(saved.start_path)).message)
        for index, step in enumerate(saved.steps):
            description = step.describe()
            if step.tool == "goto":
                await session.goto(step.url or "/")
            elif step.tool == "press":
                await session.press(step.key or "Enter")
            elif step.tool == "back":
                await session.back()
            elif step.tool == "scroll":
                await session.scroll(step.direction or "down")
            elif step.tool == "wait":
                await session.wait(step.ms or 0)
            else:
                assert step.locators is not None
                element, method = await resolve(session.page, step.locators)
                reason = f"found by its {method} locator; the other locators were updated" if method else ""
                if element is None:
                    element, reason = await _heal(session, saved, step, index, done, steps, tc, get_llm)
                    method = "ai-healer"
                if method is not None:
                    new = await locators_for(session.page, element, step.locators)
                    event = HealEvent(run_id=run_id, step_index=index, method="ai-healer" if method == "ai-healer"
                                      else "fallback-locator", old=step.locators, new=new, reason=reason)
                    outcome.heals.append(event)
                    await steps.add("info", f"Healed step {index + 1}: {event.old.describe()} → {new.describe()} "
                                    f"({event.method}: {reason})", phase="heal", test_case_index=tc.index)
                    step.locators = new
                    description = step.describe()
                await _act(session, element, step)
            parts = urlsplit(session.url)
            step.url_after = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
            done.append(description)
            outcome.steps_done = index + 1
            await record(f"Step {index + 1}: {description}")

        for assertion in saved.assertions:
            if assertion.kind == "url":
                path = urlsplit(session.url).path or "/"
                if path != assertion.value:
                    raise StepFailed(f"Expected to end on {assertion.value}, but the page is {path}")
            else:
                try:
                    await session.page.get_by_text(assertion.value).first.wait_for(state="visible", timeout=ASSERT_TIMEOUT_MS)
                except PlaywrightError:
                    raise StepFailed(f'Expected "{assertion.value}" on the page, but it is not shown') from None
        outcome.passed = True
        healed = f" ({len(outcome.heals)} step(s) healed)" if outcome.heals else ""
        outcome.reason = f"All {len(saved.steps)} steps replayed and the checks passed{healed}."
    except (StepFailed, ToolError, PlaywrightError) as exc:
        where = f"step {outcome.steps_done + 1}" if outcome.steps_done < len(saved.steps) else "the final checks"
        outcome.reason = f"Failed at {where}: {str(exc).strip().splitlines()[0]}"
        await record(outcome.reason, "error")
    return outcome


async def _heal(
    session: BrowserSession,
    saved: SavedTest,
    step: SavedStep,
    index: int,
    done: list[str],
    steps: StepRecorder,
    tc: TestCase,
    get_llm: Callable[[], LLMProvider | None],
) -> tuple[Locator, str]:
    assert step.locators is not None
    lost = step.locators.describe()
    llm = get_llm()
    if llm is None:
        raise StepFailed(f"Could not find {lost}, and no LLM is configured to heal the step")
    await steps.add("thought", f"Step {index + 1} can't find {lost} — asking the healer", phase="heal",
                    test_case_index=tc.index)
    snapshot = await session.snapshot()
    try:
        decision = await find_replacement(llm, test_title=saved.title, step=step, previous=done, snapshot=snapshot,
                                          snapshot_text=session.redact(snapshot.to_text()))
    except LLMError as exc:
        raise StepFailed(f"Could not find {lost}; the healer failed: {exc}") from None
    if decision.ref is None:
        raise StepFailed(f"Could not find {lost}; the healer found no match: {decision.reason}")
    return session.page.locator(f'[{REF_ATTRIBUTE}="{decision.ref}"]').first, decision.reason


async def handle_replay_run(
    job: Job,  # noqa: ARG001 - same signature as the agent run handler
    run: Run,
    project: Project,
    *,
    launcher_factory: "LauncherFactory",
    llm: LLMProvider | None,
    vault: SecretVault,
) -> None:
    settings = get_settings()
    assert run.id is not None and project.id is not None
    started = time.monotonic()
    run.status, run.started_at, run.error = "running", utcnow(), None
    await run.save()
    steps = await StepRecorder.for_run(run)
    query = SavedTest.find(SavedTest.project_id == project.id)
    if run.saved_test_ids:
        query = SavedTest.find(SavedTest.project_id == project.id, {"_id": {"$in": run.saved_test_ids}})
    saved_tests = await query.sort(+SavedTest.created_at).to_list()
    provider: dict[str, LLMProvider | None] = {"llm": llm}
    tried = {"llm": llm is not None}

    def get_llm() -> LLMProvider | None:
        if not tried["llm"]:
            tried["llm"] = True
            try:
                provider["llm"] = get_provider()
                if isinstance(provider["llm"], FallbackProvider):
                    async def note_switch(message: str) -> None:
                        await steps.add("info", message)
                    provider["llm"].on_switch = note_switch
            except LLMError:
                provider["llm"] = None
        return provider["llm"]

    stats: dict = {"tests": len(saved_tests), "passed": 0, "failed": 0, "healed_steps": 0}
    new_failures: list[str] = []
    try:
        await steps.add("info", f"Replaying {len(saved_tests)} saved test(s) without the LLM"
                        + (" (scheduled)" if run.trigger == "schedule" else ""), phase="replay")
        cases = []
        for i, saved in enumerate(saved_tests):
            tc = TestCase(run_id=run.id, project_id=project.id, index=i, title=saved.title, type="happy",
                          start_path=saved.start_path, steps=[s.describe() for s in saved.steps],
                          expected=assertion_text(saved))
            await tc.insert()
            cases.append(tc)
        async with launcher_factory() as launcher:
            for saved, tc in zip(saved_tests, cases, strict=True):
                tc.status, tc.started_at, tc.updated_at = "running", utcnow(), utcnow()
                await tc.save()
                async with launcher.session(project.base_url, mobile=run.options.mobile_viewport,
                                            action_delay_ms=settings.action_delay_ms,
                                            timeout_ms=min(settings.navigation_timeout_ms, 10_000), vault=vault) as session:
                    outcome = await replay_test(session, saved, tc, steps, run.id, get_llm)
                    relative = f"runs/{run.id}/test-{tc.index + 1:02d}-final.png"
                    path = storage_root() / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    await session.screenshot(path)
                    tc.final_screenshot_path, tc.final_url = relative, session.url
                tc.status = "passed" if outcome.passed else "failed"
                tc.reason, tc.steps_used = outcome.reason, outcome.steps_done
                tc.finished_at = tc.updated_at = utcnow()
                await tc.save()
                stats["passed" if outcome.passed else "failed"] += 1
                stats["healed_steps"] += len(outcome.heals)
                if not outcome.passed and saved.last_result == "passed":
                    new_failures.append(saved.title)
                saved.last_result = "passed" if outcome.passed else "failed"
                saved.last_run_id, saved.last_run_at, saved.updated_at = run.id, utcnow(), utcnow()
                saved.replays += 1
                saved.heal_history = (saved.heal_history + outcome.heals)[-MAX_HEAL_HISTORY:]
                await saved.save()
        run.status = "completed"
        await steps.add("info", f"Replay finished: {stats['passed']} passed, {stats['failed']} failed, "
                        f"{stats['healed_steps']} step(s) healed", phase="replay")
        if run.trigger == "schedule" and new_failures:
            await Notification(
                user_id=project.owner_id, project_id=project.id, run_id=run.id, kind="new_failures",
                title=f"{len(new_failures)} new failure(s) in {project.name}",
                body="These saved tests passed before and failed in the scheduled run: " + "; ".join(new_failures),
            ).insert()
    except Exception as exc:
        message = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
        logger.warning("replay run %s failed: %s", run.id, message)
        run.status, run.error = "failed", message
        await steps.add("error", message)
        if run.trigger == "schedule":
            await Notification(user_id=project.owner_id, project_id=project.id, run_id=run.id, kind="run_failed",
                               title=f"Scheduled run of {project.name} failed", body=message).insert()
        raise
    finally:
        llm_used = provider["llm"]
        stats |= {"llm_calls": llm_used.usage.calls if llm_used else 0,
                  "input_tokens": llm_used.usage.input_tokens if llm_used else 0,
                  "output_tokens": llm_used.usage.output_tokens if llm_used else 0,
                  "seconds": round(time.monotonic() - started, 1)}
        run.stats, run.finished_at = stats, utcnow()
        await run.save()
