"""Executor: runs one test case with an observe → think → act loop using only the browser tools.

Every thought and action is saved as a run_steps document (with a screenshot after each action), so the
dashboard streams it live. Credentials only appear as {{cred:…}} placeholders to the LLM and in saved steps.
"""

import time
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from app.models.test_case import TestCase
from app.services.run_steps import StepRecorder
from worker.agents import render_prompt
from worker.browser import BrowserSession, Snapshot, ToolCall, ToolError
from app.models.saved_test import Locators
from worker.browser.locators import describe_element
from worker.browser.tools import Click, Done, Select, TakeScreenshot, TakeSnapshot, Type
from worker.llm import LLMOutputError, LLMProvider

HISTORY_IN_PROMPT = 8


MAX_BATCH = 5  # actions per LLM reply (e.g. fill a whole form, then submit)
# The tools are documented in prompts/executor.md, so the full JSON Schema (~3k chars) isn't sent every turn.
DECISION_SHAPE = '{"thought": "<one or two sentences>", "actions": [<1 to 5 tool objects exactly as listed under Tools>]}'


class ExecutorDecision(BaseModel):
    thought: str = Field(max_length=800)
    actions: list[ToolCall] = Field(min_length=1, max_length=MAX_BATCH)


@dataclass
class HistoryItem:
    step: int
    thought: str
    action: dict
    result: str
    ok: bool = True
    locators: Locators | None = None
    url_after: str | None = None  # page URL after the action  # recorded for element actions, so a passing test can be saved and replayed

    def to_line(self) -> str:
        action = ", ".join(f"{k}={v!r}" for k, v in self.action.items() if k != "tool")
        return f"{self.step}. {self.thought} → {self.action['tool']}({action}) → {self.result}"


@dataclass
class ExecutionResult:
    done: Done | None = None
    steps_used: int = 0
    history: list[HistoryItem] = field(default_factory=list)
    start_snapshot: Snapshot | None = None  # first page seen (to pick assertions: what changed by the end)
    final_snapshot: Snapshot | None = None
    final_screenshot: bytes | None = None
    error: str | None = None  # the executor itself failed (bad LLM output); not a site bug
    stop_reason: str = ""


def format_history(history: list[HistoryItem]) -> str:
    if not history:
        return "Nothing done yet: the start page was just opened."
    earlier = len(history) - HISTORY_IN_PROMPT
    lines = [f"({earlier} earlier steps not shown)"] if earlier > 0 else []
    lines += [item.to_line() for item in history[-HISTORY_IN_PROMPT:]]
    return "Steps so far:\n" + "\n".join(lines)


async def execute_test(
    llm: LLMProvider,
    session: BrowserSession,
    steps: StepRecorder,
    test: TestCase,
    *,
    max_steps: int,
    timeout_seconds: float,
    credential_placeholders: list[str],
) -> ExecutionResult:
    """Run one test case. Raises LLMError (provider down/quota) so the orchestrator can stop the run."""
    result = ExecutionResult()
    deadline = time.monotonic() + timeout_seconds
    tag = {"phase": "execute", "test_case_index": test.index}

    async def record_action(kind: str, message: str, action: dict) -> None:
        absolute, relative = steps.screenshot_path()
        shot = await session.screenshot(absolute)
        result.final_screenshot = shot
        await steps.add(kind, session.redact(message), action=action, url=session.url,  # type: ignore[arg-type]
                        screenshot_path=relative, **tag)

    try:
        opened = await session.goto(test.start_path)
        await record_action("action", opened.message, {"tool": "goto", "url": test.start_path})
    except ToolError as exc:
        await steps.add("error", str(exc), url=session.url, **tag)
        result.error, result.stop_reason = str(exc), "could not open the start page"
        return result

    credentials = "\n".join(f"- {p}" for p in credential_placeholders) or "(none)"
    step_list = "\n".join(f"{i}. {s}" for i, s in enumerate(test.steps, start=1))

    while result.done is None:
        if result.steps_used >= max_steps:
            result.stop_reason = f"step limit of {max_steps} reached"
            break
        if time.monotonic() > deadline:
            result.stop_reason = f"time limit of {timeout_seconds:.0f}s reached"
            break
        snapshot = await session.snapshot()
        result.final_snapshot = snapshot
        if result.start_snapshot is None:
            result.start_snapshot = snapshot
        snapshot_text = session.redact(snapshot.to_text())
        prompt = render_prompt(
            "executor",
            title=test.title,
            test_type=test.type,
            steps=step_list,
            expected=test.expected,
            credentials=credentials,
            step_number=result.steps_used + 1,
            max_steps=max_steps,
            max_batch=MAX_BATCH,
            history=format_history(result.history),
            snapshot=snapshot_text,
        )
        try:
            decision = await llm.generate_json(prompt, ExecutorDecision, schema_hint=DECISION_SHAPE)
        except LLMOutputError as exc:
            await steps.add("error", f"The model's reply was unusable: {exc}", url=session.url, **tag)
            result.error, result.stop_reason = str(exc), "invalid model output"
            break

        actions = decision.actions[: max_steps - result.steps_used]
        if len(actions) > 1 and any(isinstance(a, Done) for a in actions):
            # The model must see the outcome before deciding: run the other actions, ask again for the verdict.
            actions = [a for a in actions if not isinstance(a, Done)]
        await steps.add("thought", session.redact(decision.thought), url=session.url, snapshot=snapshot_text,
                        action={"actions": [a.model_dump() for a in actions]}, **tag)
        start_url = session.url
        for position, action in enumerate(actions):
            result.steps_used += 1
            action_dict = action.model_dump()
            thought = decision.thought if position == 0 else "(same plan)"
            if isinstance(action, Done):
                result.done = action
                result.stop_reason = f"executor finished: {action.result}"
                await steps.add("info", f"Executor says {action.result}: {session.redact(action.reason)}", **tag)
                break
            if isinstance(action, TakeSnapshot):
                result.history.append(HistoryItem(result.steps_used, thought, action_dict, "took a new snapshot"))
                break  # the next turn starts with a fresh snapshot anyway
            if isinstance(action, TakeScreenshot):
                await record_action("observation", "Screenshot", action_dict)
                result.history.append(HistoryItem(result.steps_used, thought, action_dict, "took a screenshot"))
                continue
            locators = None
            if isinstance(action, Click | Type | Select):
                try:
                    locators = await describe_element(session.page, action.ref, snapshot.find(action.ref))
                except Exception:  # noqa: BLE001 - stale ref etc.: the action below reports the real problem
                    locators = None
            try:
                outcome = await session.execute(action)
                message, kind, failed = outcome.message, "action", False
            except ToolError as exc:
                message, kind, failed = f"Failed: {exc}", "observation", True
            await record_action(kind, message, action_dict)
            result.history.append(HistoryItem(result.steps_used, thought, action_dict, session.redact(message),
                                              ok=not failed, locators=locators, url_after=session.url))
            if failed or session.url != start_url:
                break  # refs from this snapshot may no longer be valid: look again before acting

    if result.done is None and not result.error:
        await steps.add("info", f"Executor stopped: {result.stop_reason}", **tag)
    # What the judge looks at: the page as it is now.
    result.final_snapshot = await session.snapshot()
    result.final_screenshot = await session.screenshot()
    return result
