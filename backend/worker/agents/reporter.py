"""Reporter: turns a failed test into a bug report and deduplicates it against the project's existing bugs.

Retrieval-augmented: the bugs shown to the LLM are the ones most similar to this failure, found by semantic search
over the project's whole bug history in the vector store (app/rag), including old bugs marked fixed, so a
regression is recognised even months later. Without the store it falls back to the most recently seen bugs.
The LLM writes the report and says which shown bug (if any) is the same problem; an exact title match is
also treated as a duplicate. A duplicate updates the existing bug (occurrences, runs, latest screenshot);
a bug marked "fixed" that shows up again is reopened (regression); an "ignored" bug stays ignored.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from beanie import PydanticObjectId
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.models.base import utcnow
from app.models.bug import Bug, title_key
from app.models.test_case import TestCase
from app.rag import bug_document, get_knowledge_base, remember
from worker.agents import render_prompt
from worker.agents.executor import ExecutionResult
from worker.llm import LLMProvider

logger = logging.getLogger("qa_pilot.reporter")

MAX_EXISTING_BUGS = 40  # fallback without the vector store: the most recently seen bugs
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
    compared_with: int = 0  # how many existing bugs the LLM was shown
    retrieval: str = "recent"  # "semantic" (vector search) or "recent" (fallback)
    top_match: tuple[str, float] | None = None  # (title, similarity) of the most similar existing bug


def _unchanged(text: str) -> str:
    return text


def format_existing(bugs: list[Bug], similarity: list[float] | None = None) -> str:
    if not bugs:
        return "(none yet)"
    scores: list[float | None] = list(similarity) if similarity else [None] * len(bugs)
    return "\n".join(
        f"[{i}] ({b.status}, {b.severity}{'' if s is None else f', {s:.0%} similar'}) {b.title} — actual: {b.actual[:160]}"
        for i, (b, s) in enumerate(zip(bugs, scores, strict=True), 1)
    )


async def similar_bugs(project_id: PydanticObjectId, query: str, k: int) -> list[tuple[Bug, float]] | None:
    """The k bugs of the project most similar to `query` (vector search, then loaded from MongoDB), or None
    when the vector store is unavailable."""
    kb = get_knowledge_base()
    if kb is None:
        return None
    try:
        hits = await kb.search(project_id, query, kinds=["bug"], k=k)
    except Exception as exc:  # noqa: BLE001 - fall back to recent bugs
        logger.warning("bug retrieval failed: %s: %s", type(exc).__name__, exc)
        return None
    ids = [PydanticObjectId(h.ref_id) for h in hits if PydanticObjectId.is_valid(h.ref_id)]
    found = {str(b.id): b for b in await Bug.find({"_id": {"$in": ids}, "project_id": project_id}).to_list()}
    return [(found[h.ref_id], h.similarity) for h in hits if h.ref_id in found]


def failure_query(test: TestCase, verdict_reason: str, final_url: str) -> str:
    """What the failure is about, phrased like a bug document, for the similarity search."""
    return f"{test.title}\nPage: {final_url}\nExpected: {test.expected}\nActual: {verdict_reason}"


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
    final_url = execution.final_snapshot.url if execution.final_snapshot else "unknown"
    retrieved = await similar_bugs(project_id, redact(failure_query(test, verdict_reason, final_url)),
                                   get_settings().rag_bug_candidates)
    if retrieved:
        existing, scores = [b for b, _ in retrieved], [s for _, s in retrieved]
    else:  # no vector store, or nothing indexed yet
        existing = await Bug.find(Bug.project_id == project_id).sort(-Bug.last_seen_at).limit(MAX_EXISTING_BUGS).to_list()
        scores = None
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
        final_url=final_url,
        snapshot=redact(snapshot),
        existing_bugs=format_existing(existing, scores),
    )
    report = await llm.generate_json(prompt, BugReport)

    duplicate: Bug | None = None
    if report.duplicate_of is not None and 1 <= report.duplicate_of <= len(existing):
        duplicate = existing[report.duplicate_of - 1]
    if duplicate is None:  # the model missed an exact repeat
        key = title_key(report.title)
        duplicate = await Bug.find_one(Bug.project_id == project_id, Bug.title_key == key)

    found = {"compared_with": len(existing), "retrieval": "semantic" if scores else "recent",
             "top_match": (existing[0].title, scores[0]) if scores else None}
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
        return ReportOutcome(bug=duplicate, created=False, reopened=reopened, **found)

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
    await remember(bug_document(bug))  # so a later test in this same run can match it
    return ReportOutcome(bug=bug, created=True, **found)
