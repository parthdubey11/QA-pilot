"""What QA Pilot remembers: MongoDB documents -> LangChain Documents for the vector store, and safe helpers to
index / forget them. Indexing never raises: a run must not fail because the memory is unavailable."""

import logging
from collections.abc import Callable

from langchain_core.documents import Document

from app.models.accessibility import A11yIssue
from app.models.bug import Bug
from app.models.saved_test import HealEvent, SavedTest
from app.models.test_case import TestCase
from app.rag.store import KnowledgeBase, get_knowledge_base

logger = logging.getLogger("qa_pilot.rag")

FINISHED_TEST_STATUSES = {"passed", "failed", "blocked", "error"}


def _unchanged(text: str) -> str:
    return text


def _meta(kind: str, ref_id: object, project_id: object, title: str, **extra: object) -> dict:
    meta = {"kind": kind, "ref_id": str(ref_id), "project_id": str(project_id), "title": title[:200]}
    return meta | {k: str(v) for k, v in extra.items() if v is not None}


def bug_document(bug: Bug) -> tuple[str, Document]:
    steps = "; ".join(bug.steps[:12])
    text = (f"Bug ({bug.severity}): {bug.title}\nPage: {bug.url or 'unknown'}\nSteps: {steps}\n"
            f"Expected: {bug.expected}\nActual: {bug.actual}\nSuggested fix: {bug.suggested_fix}")
    return f"bug:{bug.id}", Document(page_content=text, metadata=_meta(
        "bug", bug.id, bug.project_id, bug.title, run_id=bug.first_run_id, severity=bug.severity))


def test_document(tc: TestCase, redact: Callable[[str], str] = _unchanged) -> tuple[str, Document]:
    steps = "; ".join(tc.steps[:12])
    text = redact(f"Test [{tc.type}] {tc.title} — {tc.status}\nSteps: {steps}\nExpected: {tc.expected}\n"
                  f"Result: {tc.reason or '(no reason recorded)'}\nFinal page: {tc.final_url or 'unknown'}")
    return f"test:{tc.id}", Document(page_content=text, metadata=_meta(
        "test", tc.id, tc.project_id, tc.title, run_id=tc.run_id, status=tc.status,
        bug_id=tc.bug_id))


def a11y_document(issue: A11yIssue) -> tuple[str, Document]:
    """One per (page, rule): a later run's finding replaces the earlier one instead of piling up copies."""
    elements = "; ".join(n.summary or n.target for n in issue.nodes[:5])
    wcag = ", ".join(issue.wcag) or "n/a"
    text = (f"Accessibility issue ({issue.impact}, WCAG {wcag}) on {issue.page_path}: {issue.title}\n"
            f"Rule: {issue.rule_id} ({issue.source})\n{issue.description}\nElements: {elements or 'n/a'}\n"
            f"How to fix: {issue.how_to_fix}")
    return f"a11y:{issue.project_id}:{issue.page_path}:{issue.rule_id}", Document(page_content=text, metadata=_meta(
        "a11y", issue.id, issue.project_id, f"{issue.title} ({issue.page_path})", run_id=issue.run_id,
        impact=issue.impact, rule_id=issue.rule_id))


def heal_document(saved: SavedTest, event: HealEvent) -> tuple[str, Document]:
    text = (f"Repair in saved test '{saved.title}', step {event.step_index + 1}: the element "
            f"{event.old.describe()} was no longer found; {event.method} replaced it with {event.new.describe()}. "
            f"Reason: {event.reason}")
    return (f"heal:{saved.id}:{event.run_id}:{event.step_index}",
            Document(page_content=text, metadata=_meta(
                "heal", saved.id, saved.project_id, f"{event.old.describe()} → {event.new.describe()}",
                run_id=event.run_id, saved_test_title=saved.title)))


async def remember(*items: tuple[str, Document], kb: KnowledgeBase | None = None) -> bool:
    """Index documents; False (and a log line) if the memory is unavailable."""
    kb = kb or get_knowledge_base()
    if kb is None or not items:
        return False
    try:
        await kb.upsert(list(items))
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not index %d document(s): %s: %s", len(items), type(exc).__name__, exc)
        return False
    return True


async def forget_project(project_id: object) -> None:
    kb = get_knowledge_base()
    if kb is None:
        return
    try:
        await kb.delete_project(project_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not delete project %s from the knowledge base: %s", project_id, exc)
