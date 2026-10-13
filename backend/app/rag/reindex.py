"""Rebuild the vector store from MongoDB (the source of truth).

The worker runs `reindex_if_empty()` at startup, so data from before RAG existed (or a lost Chroma volume) is
indexed automatically. By hand: `python -m app.rag.reindex` (all projects) or `python -m app.rag.reindex <id>`.
"""

import asyncio
import logging
import sys

from beanie import PydanticObjectId
from langchain_core.documents import Document

from app.models.accessibility import A11yIssue
from app.models.bug import Bug
from app.models.saved_test import SavedTest
from app.models.test_case import TestCase
from app.rag.memory import FINISHED_TEST_STATUSES, a11y_document, bug_document, heal_document, test_document
from app.rag.store import KnowledgeBase, get_knowledge_base

logger = logging.getLogger("qa_pilot.rag")
BATCH = 64


async def reindex(kb: KnowledgeBase, project_id: PydanticObjectId | None = None) -> dict[str, int]:
    """Index every bug, finished test case, a11y issue and heal event (of one project, or all). Returns how many
    documents of each kind were written (an a11y rule seen on the same page in several runs counts once per batch)."""
    scope: dict = {"project_id": project_id} if project_id is not None else {}
    counts = {"bug": 0, "test": 0, "a11y": 0, "heal": 0}
    pending: dict[str, Document] = {}  # by id: Chroma rejects a batch with the same id twice

    async def add(kind: str, item: tuple[str, Document]) -> None:
        if item[0] not in pending:
            counts[kind] += 1
        pending[item[0]] = item[1]
        if len(pending) >= BATCH:
            await kb.upsert(list(pending.items()))
            pending.clear()

    async for bug in Bug.find(scope):
        await add("bug", bug_document(bug))
    async for tc in TestCase.find(scope | {"status": {"$in": sorted(FINISHED_TEST_STATUSES)}}):
        await add("test", test_document(tc))
    async for issue in A11yIssue.find(scope).sort(+A11yIssue.created_at):  # oldest first: the latest run wins
        await add("a11y", a11y_document(issue))
    async for saved in SavedTest.find(scope):
        for event in saved.heal_history:
            await add("heal", heal_document(saved, event))
    await kb.upsert(list(pending.items()))
    return counts


async def reindex_if_empty() -> dict[str, int] | None:
    """Index everything when the store is empty but MongoDB has bugs or finished tests; never raises."""
    kb = get_knowledge_base()
    if kb is None:
        return None
    try:
        if await kb.count() > 0:
            return None
        if await Bug.find().count() == 0 and await TestCase.find({"status": {"$ne": "pending"}}).count() == 0:
            return None
        return await reindex(kb)
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not rebuild the knowledge base: %s: %s", type(exc).__name__, exc)
        return None


async def _main(argv: list[str]) -> None:
    from app.core.db import close_db, init_db_with_retry  # noqa: PLC0415

    await init_db_with_retry()
    try:
        kb = get_knowledge_base()
        if kb is None:
            raise SystemExit("Knowledge base unavailable: check RAG_ENABLED / CHROMA_URL")
        project_id = PydanticObjectId(argv[0]) if argv else None
        print(await reindex(kb, project_id))  # noqa: T201
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(_main(sys.argv[1:]))
