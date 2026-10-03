"""Reporter: turns a failed test into a bug report and deduplicates it against the project's existing bugs.

The LLM writes the report and says which existing bug (if any) is the same problem; an exact title match is
also treated as a duplicate. A duplicate updates the existing bug (occurrences, runs, latest screenshot);
a bug marked "fixed" that shows up again is reopened (regression); an "ignored" bug stays ignored.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from beanie import PydanticObjectId
from pydantic import BaseModel, Field

from app.models.base import utcnow
from app.models.bug import Bug, title_key
from app.models.test_case import TestCase
from worker.agents import render_prompt
from worker.agents.executor import ExecutionResult
from worker.llm import LLMProvider

MAX_EXISTING_BUGS = 40
MAX_HISTORY_LINES = 25


class BugReport(BaseModel):
    title: str = Field(min_length=3, max_length=160)
    severity: Literal["critical", "high", "medium", "low"]
    steps: list[str] = Field(min_length=1, max_length=20)
    expected: str = Field(min_length=1, max_length=1000)
    actual: str = Field(min_length=1, max_length=1000)
    suggested_fix: str = Field(min_length=1, max_length=1000)
    duplicate_of: int | None = None  # [N] in the list of already reported bugs


@dataclass
class ReportOutcome:
    bug: Bug
    created: bool  # False = merged into an existing bug
    reopened: bool = False


def _unchanged(text: str) -> str:
    return text


def format_existing(bugs: list[Bug]) -> str:
    if not bugs:
        return "(none yet)"
    return "\n".join(f"[{i}] ({b.status}, {b.severity}) {b.title} — actual: {b.actual[:160]}" for i, b in enumerate(bugs, 1))


async def report_bug(
    llm: LLMProvider,
    *,
    project_id: PydanticObjectId,
    run_id: PydanticObjectId,
    test: TestCase,
    verdict_reason: str,
    execution: ExecutionResult,
    screenshot_path: str | None,
    redact: Callable[[str], str] = _unchanged,
) -> ReportOutcome:
    """redact: hides credential values before they reach the LLM or the saved bug (SecretVault.redact)."""
    existing = await Bug.find(Bug.project_id == project_id).sort(-Bug.last_seen_at).limit(MAX_EXISTING_BUGS).to_list()
    history = [item.to_line() for item in execution.history][-MAX_HISTORY_LINES:]
    snapshot = execution.final_snapshot.to_text() if execution.final_snapshot else "(no snapshot)"
    prompt = render_prompt(
        "reporter",
        title=test.title,
        test_type=test.type,
        planned_steps="\n".join(f"{i}. {s}" for i, s in enumerate(test.steps, 1)),
        expected=test.expected,
        verdict=redact(verdict_reason),
        history=redact("\n".join(history)) or "(no actions)",
        final_url=execution.final_snapshot.url if execution.final_snapshot else "unknown",
        snapshot=redact(snapshot),
        existing_bugs=format_existing(existing),
    )
    report = await llm.generate_json(prompt, BugReport)

    duplicate: Bug | None = None
    if report.duplicate_of is not None and 1 <= report.duplicate_of <= len(existing):
        duplicate = existing[report.duplicate_of - 1]
    if duplicate is None:  # the model missed an exact repeat
        key = title_key(report.title)
        duplicate = await Bug.find_one(Bug.project_id == project_id, Bug.title_key == key)

    now = utcnow()
    url = execution.final_snapshot.url if execution.final_snapshot else None
    if duplicate is not None:
        reopened = duplicate.status == "fixed"
        if reopened:
            duplicate.status, duplicate.reopened = "open", True
        if run_id not in duplicate.run_ids:
            duplicate.run_ids.append(run_id)
        duplicate.occurrences += 1
        duplicate.last_seen_at = duplicate.updated_at = now
        duplicate.screenshot_path = screenshot_path or duplicate.screenshot_path
        duplicate.url = url or duplicate.url
        await duplicate.save()
        return ReportOutcome(bug=duplicate, created=False, reopened=reopened)

    bug = Bug(
        project_id=project_id,
        title=report.title,
        title_key=title_key(report.title),
        severity=report.severity,
        steps=[redact(s) for s in report.steps],
        expected=redact(report.expected),
        actual=redact(report.actual),
        suggested_fix=report.suggested_fix,
        url=url,
        screenshot_path=screenshot_path,
        run_ids=[run_id],
        first_run_id=run_id,
        first_test_title=test.title,
        first_seen_at=now,
        last_seen_at=now,
    )
    await bug.insert()
    return ReportOutcome(bug=bug, created=True)
