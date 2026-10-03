"""Judge: decides pass / fail / blocked for one executed test case, using the final snapshot and screenshot."""

from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, Field

from app.models.test_case import TestCase
from worker.agents import render_prompt
from worker.agents.executor import ExecutionResult
from worker.llm import LLMProvider

MAX_HISTORY_LINES = 30


class Verdict(BaseModel):
    result: Literal["pass", "fail", "blocked"]
    reason: str = Field(min_length=1, max_length=1500)


def _unchanged(text: str) -> str:
    return text


async def judge_test(
    llm: LLMProvider, test: TestCase, execution: ExecutionResult, *, redact: Callable[[str], str] = _unchanged
) -> Verdict:
    """redact: hides credential values before they reach the LLM (BrowserSession.redact)."""
    lines = [item.to_line() for item in execution.history]
    if len(lines) > MAX_HISTORY_LINES:
        lines = lines[:5] + [f"… ({len(lines) - MAX_HISTORY_LINES} steps omitted) …"] + lines[-(MAX_HISTORY_LINES - 5):]
    claim = (
        f'The agent\'s own opinion: {execution.done.result} — "{execution.done.reason}"'
        if execution.done
        else "The agent did not give an opinion."
    )
    snapshot = execution.final_snapshot.to_text() if execution.final_snapshot else "(no snapshot)"
    prompt = render_prompt(
        "judge",
        title=test.title,
        test_type=test.type,
        steps="\n".join(f"{i}. {s}" for i, s in enumerate(test.steps, start=1)),
        expected=test.expected,
        history="\n".join(lines) or "(no actions)",
        stop_reason=execution.stop_reason or "unknown",
        executor_claim=redact(claim),
        snapshot=redact(snapshot),
    )
    images = [execution.final_screenshot] if execution.final_screenshot else None
    return await llm.generate_json(prompt, Verdict, images=images)
