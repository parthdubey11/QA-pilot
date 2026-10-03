"""Turn a passing test into a SavedTest: replayable steps (with locators) plus assertions."""

from collections.abc import Callable
from urllib.parse import urlsplit

from beanie import PydanticObjectId

from app.models.base import utcnow
from app.models.bug import title_key
from app.models.saved_test import Assertion, SavedStep, SavedTest
from app.models.test_case import TestCase
from worker.agents.executor import ExecutionResult, HistoryItem
from worker.browser import Snapshot

ELEMENT_TOOLS = {"click", "type", "select"}
REPLAYABLE = ELEMENT_TOOLS | {"goto", "press", "back", "scroll", "wait"}
MAX_TEXT_ASSERTIONS = 3


def _path(url: str | None) -> str | None:
    if not url:
        return None
    parts = urlsplit(url)
    return (parts.path or "/") + (f"?{parts.query}" if parts.query else "")


def saved_steps(history: list[HistoryItem]) -> list[SavedStep] | None:
    """Replayable steps from the executor history, or None if an element action has no locators."""
    steps: list[SavedStep] = []
    for item in history:
        tool = item.action.get("tool")
        if not item.ok or tool not in REPLAYABLE:
            continue
        if tool in ELEMENT_TOOLS and item.locators is None:
            return None
        steps.append(SavedStep(
            tool=tool, locators=item.locators, url=item.action.get("url"), text=item.action.get("text"),
            value=item.action.get("value"), key=item.action.get("key"), ms=item.action.get("ms"),
            direction=item.action.get("direction"), note=item.thought if item.thought != "(same plan)" else "",
            url_after=_path(item.url_after),
        ))
    return steps


def pick_assertions(start: Snapshot | None, final: Snapshot, redact: Callable[[str], str]) -> list[Assertion]:
    """What must be true at the end: the final page path, plus up to 3 texts that appeared during the test
    (status/alert messages first, then headings, then other text)."""
    assertions = [Assertion(kind="url", value=urlsplit(final.url).path or "/")]
    before = {(e.text or e.name or "").strip() for e in start.elements} if start else set()
    rank = {"status": 0, "alert": 0, "heading": 1, "text": 2}
    candidates = sorted(
        (e for e in final.elements if e.role in rank), key=lambda e: rank[e.role]
    )
    seen: set[str] = set()
    for element in candidates:
        text = (element.text or element.name or "").strip()
        if not 3 <= len(text) <= 120 or text in before or text in seen or redact(text) != text:
            continue  # unchanged, too short/long, or contains a credential value
        seen.add(text)
        assertions.append(Assertion(kind="text", value=text))
        if len(seen) >= MAX_TEXT_ASSERTIONS:
            break
    return assertions


async def save_passing_test(
    *,
    project_id: PydanticObjectId,
    run_id: PydanticObjectId,
    test: TestCase,
    execution: ExecutionResult,
    redact: Callable[[str], str],
) -> SavedTest | None:
    """Save (or update, by title) a passing test. Returns None if it can't be replayed reliably."""
    steps = saved_steps(execution.history)
    if not steps or not any(s.tool in ELEMENT_TOOLS for s in steps) or execution.final_snapshot is None:
        return None
    assertions = pick_assertions(execution.start_snapshot, execution.final_snapshot, redact)
    key = title_key(test.title)
    existing = await SavedTest.find_one(SavedTest.project_id == project_id, SavedTest.title_key == key)
    now = utcnow()
    if existing is not None:
        existing.steps, existing.assertions, existing.start_path = steps, assertions, test.start_path
        existing.expected, existing.source_run_id = test.expected, run_id
        existing.last_result, existing.last_run_id, existing.last_run_at, existing.updated_at = "passed", run_id, now, now
        await existing.save()
        return existing
    saved = SavedTest(project_id=project_id, title=test.title, title_key=key, start_path=test.start_path, steps=steps,
                      assertions=assertions, expected=test.expected, source_run_id=run_id, last_run_id=run_id,
                      last_run_at=now)
    await saved.insert()
    return saved
