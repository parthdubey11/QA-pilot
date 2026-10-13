"""The QA memory of every project: a ChromaDB vector store, used through LangChain.

What goes in (one LangChain Document each, tagged with project_id / kind / ref_id):
- "bug": every bug report (title, page, steps, expected/actual, suggested fix);
- "test": every finished test case and its verdict;
- "a11y": every accessibility rule violated on a page (one per page + rule, the latest run wins);
- "heal": every repair the Healer (or a fallback locator) made to a saved test.

MongoDB stays the source of truth: the store only holds text + ids for semantic search, so a bug's status is read
from MongoDB after retrieval and a status change needs no re-indexing. Texts are stored already redacted
(credentials never reach the store). Chroma's client is synchronous, so calls run in a thread.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Literal

import chromadb
from chromadb.config import Settings as ChromaSettings
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings

from app.core.config import Settings, get_settings
from app.rag.embeddings import make_embeddings

logger = logging.getLogger("qa_pilot.rag")

Kind = Literal["bug", "test", "a11y", "heal"]
KINDS: tuple[Kind, ...] = ("bug", "test", "a11y", "heal")


@dataclass
class Hit:
    """One retrieved document. similarity: cosine similarity (1 = same meaning, ~0 = unrelated)."""

    id: str
    kind: Kind
    ref_id: str
    text: str
    similarity: float
    metadata: dict = field(default_factory=dict)

    @property
    def title(self) -> str:
        return str(self.metadata.get("title") or self.text.splitlines()[0])


class KnowledgeBase:
    def __init__(self, client: chromadb.ClientAPI, embeddings: Embeddings, collection: str):
        self.client = client
        self.collection_name = collection
        self.store = Chroma(
            collection_name=collection,
            client=client,
            embedding_function=embeddings,
            collection_configuration={"hnsw": {"space": "cosine"}},
        )

    async def upsert(self, items: list[tuple[str, Document]]) -> None:
        """Add or replace documents by id."""
        if items:
            ids, docs = zip(*items, strict=True)
            await asyncio.to_thread(self.store.add_documents, list(docs), ids=list(ids))

    async def search(
        self,
        project_id: object,
        query: str,
        *,
        kinds: tuple[Kind, ...] | list[Kind] | None = None,
        k: int = 8,
        min_similarity: float = 0.0,
        exclude_ids: tuple[str, ...] = (),
    ) -> list[Hit]:
        """The k documents of this project most similar to `query`, most similar first."""
        conditions: list[dict] = [{"project_id": str(project_id)}]
        if kinds:
            conditions.append({"kind": {"$in": list(kinds)}})
        where = conditions[0] if len(conditions) == 1 else {"$and": conditions}
        results = await asyncio.to_thread(
            self.store.similarity_search_with_score, query, k=k + len(exclude_ids), filter=where
        )
        hits = []
        for doc, distance in results:
            meta = dict(doc.metadata)
            hit = Hit(id=str(doc.id or ""), kind=meta.get("kind", "bug"), ref_id=str(meta.get("ref_id", "")),
                      text=doc.page_content, similarity=round(max(0.0, 1.0 - float(distance)), 4), metadata=meta)
            if hit.id not in exclude_ids and hit.similarity >= min_similarity:
                hits.append(hit)
        return hits[:k]

    async def count(self, project_id: object | None = None) -> int:
        where = {"project_id": str(project_id)} if project_id is not None else None
        result = await asyncio.to_thread(self.store.get, where=where, include=[])
        return len(result["ids"])

    async def delete(self, ids: list[str]) -> None:
        if ids:
            await asyncio.to_thread(self.store.delete, ids)

    async def delete_project(self, project_id: object) -> None:
        await asyncio.to_thread(self.store.delete, None, where={"project_id": str(project_id)})


# ---------- the process-wide instance ----------

_kb: KnowledgeBase | None = None
_failed_at: float | None = None
RETRY_SECONDS = 30.0


def make_client(settings: Settings) -> chromadb.ClientAPI:
    chroma_settings = ChromaSettings(anonymized_telemetry=False)
    if settings.chroma_url:
        from urllib.parse import urlsplit  # noqa: PLC0415

        url = urlsplit(settings.chroma_url)
        return chromadb.HttpClient(host=url.hostname or "localhost", port=url.port or 8000, ssl=url.scheme == "https",
                                   settings=chroma_settings)
    from app.services.run_steps import storage_root  # noqa: PLC0415

    return chromadb.PersistentClient(path=str(storage_root() / "chroma"), settings=chroma_settings)


def get_knowledge_base() -> KnowledgeBase | None:
    """The shared store, or None when RAG is off or Chroma can't be reached (retried after RETRY_SECONDS).
    Callers carry on without memory: RAG improves results but is never required for a run."""
    global _kb, _failed_at  # noqa: PLW0603
    settings = get_settings()
    if _kb is not None or not settings.rag_enabled:
        return _kb
    if _failed_at is not None and time.monotonic() - _failed_at < RETRY_SECONDS:
        return None
    try:
        _kb = KnowledgeBase(make_client(settings), make_embeddings(settings), settings.chroma_collection)
        _failed_at = None
    except Exception as exc:  # noqa: BLE001 - Chroma down / misconfigured: run without memory
        _failed_at = time.monotonic()
        logger.warning("knowledge base unavailable (%s: %s)", type(exc).__name__, exc)
    return _kb


def set_knowledge_base(kb: KnowledgeBase | None) -> None:
    """Replace the shared store (tests)."""
    global _kb, _failed_at  # noqa: PLW0603
    _kb, _failed_at = kb, None
