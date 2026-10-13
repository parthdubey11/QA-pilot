"""Assistant: answers questions about a project's QA history with corrective RAG, built as a LangGraph graph.

    START → retrieve → grade ──(relevant documents)──────────────────► generate → END
               ▲          ├──(none relevant, not rewritten yet)──► rewrite ─┐
               └──────────┼─────────────────────────────────────────────────┘
                          └──(nothing found after that)──────────► generate (no LLM call)

- retrieve: semantic search (ChromaDB via LangChain) over the project's bugs, test results, accessibility issues
  and self-healing repairs; bug hits get their live status (open / fixed / ignored) from MongoDB.
- grade: the LLM keeps only the documents that help answer the question (one call for all of them).
- rewrite: when none do, the LLM rephrases the search query once and the graph retrieves again.
- generate: the LLM answers from the kept documents only, citing them as [n].

Each LLM step is a LangChain LCEL chain, PromptTemplate (prompts/assistant_*.md) | QA Pilot's own provider
(Gemini / Groq / Ollama) wrapped as a Runnable, so the provider layer, retries and JSON validation are shared with
the other agents. A typical question costs 2 LLM calls, at most 4.
"""

import operator
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Annotated, Any, TypedDict, TypeVar

from beanie import PydanticObjectId
from langchain_core.prompt_values import PromptValue
from langchain_core.prompts import PromptTemplate
from langchain_core.runnables import Runnable, RunnableLambda
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from app.models.bug import Bug
from app.rag import Hit, KnowledgeBase
from worker.agents import load_prompt
from worker.llm import LLMProvider

T = TypeVar("T", bound=BaseModel)

MAX_DOCUMENTS = 8  # retrieved per search
MAX_REWRITES = 1
EXCERPT_CHARS = 700
NOTHING_FOUND = ("I couldn't find anything about that in this project's testing history. Bugs, test results, "
                 "accessibility issues and self-healing repairs are searchable once a run has produced them.")
KIND_LABELS = {"bug": "bug report", "test": "test result", "a11y": "accessibility issue", "heal": "self-healing repair"}


class Grade(BaseModel):
    relevant: list[int] = Field(default_factory=list, max_length=20, description="[N] of the helpful documents")


class Rewrite(BaseModel):
    query: str = Field(min_length=2, max_length=300)


class Answer(BaseModel):
    answer: str = Field(min_length=1, max_length=3000)
    cited: list[int] = Field(default_factory=list, max_length=20)


class AskState(TypedDict, total=False):
    question: str
    query: str
    hits: list[Hit]
    relevant: list[Hit]
    rewrites: int
    answer: str
    sources: list[tuple[int, Hit]]  # (the [n] cited in the answer, document)
    trace: Annotated[list[str], operator.add]  # each node appends what it did


@dataclass
class AskResult:
    answer: str
    sources: list[tuple[int, Hit]]
    trace: list[str] = field(default_factory=list)
    llm_calls: int = 0


def structured_llm(llm: LLMProvider, schema: type[T]) -> Runnable[PromptValue, T]:
    """A QA Pilot LLM provider as a LangChain Runnable: prompt value in, validated Pydantic object out."""

    async def call(prompt: PromptValue) -> T:
        return await llm.generate_json(prompt.to_string(), schema)

    return RunnableLambda(call, name=f"{llm.name}:{schema.__name__}")


def chain(prompt_name: str, llm: LLMProvider, schema: type[T]) -> Runnable[dict[str, Any], T]:
    """LCEL: prompts/<prompt_name>.md as a PromptTemplate | the LLM with structured output."""
    return PromptTemplate.from_template(load_prompt(prompt_name)) | structured_llm(llm, schema)


def format_documents(hits: list[Hit]) -> str:
    lines = []
    for i, hit in enumerate(hits, 1):
        live = hit.metadata.get("live")
        text = hit.text if len(hit.text) <= EXCERPT_CHARS else hit.text[:EXCERPT_CHARS] + "…"
        lines.append(f"[{i}] ({KIND_LABELS.get(hit.kind, hit.kind)}{f'; {live}' if live else ''})\n{text}")
    return "\n\n".join(lines)


def _picked(numbers: list[int], hits: list[Hit]) -> list[int]:
    return [n for n in dict.fromkeys(numbers) if 1 <= n <= len(hits)]


def _bug_ref(hit: Hit) -> str | None:
    """The bug a hit is about: the bug itself, or the bug a failed test was reported as."""
    ref = hit.ref_id if hit.kind == "bug" else hit.metadata.get("bug_id") if hit.kind == "test" else None
    return ref if ref and PydanticObjectId.is_valid(ref) else None


async def with_live_status(hits: list[Hit], project_id: PydanticObjectId) -> list[Hit]:
    """Bug hits, and test results that were reported as a bug, get the bug's current status from MongoDB (the
    source of truth, so the answer knows what is fixed); bugs deleted since are dropped."""
    ids = list({PydanticObjectId(ref) for hit in hits if (ref := _bug_ref(hit))})
    bugs = {str(b.id): b for b in await Bug.find({"_id": {"$in": ids}, "project_id": project_id}).to_list()} if ids else {}
    kept = []
    for hit in hits:
        bug = bugs.get(_bug_ref(hit) or "")
        if hit.kind == "bug" and bug is None:
            continue
        if bug is not None:
            status = (f"status {bug.status}{', reopened' if bug.reopened else ''}, seen {bug.occurrences}× "
                      f"from {bug.first_seen_at:%Y-%m-%d} to {bug.last_seen_at:%Y-%m-%d}")
            hit.metadata["live"] = status if hit.kind == "bug" else f'reported as bug "{bug.title}" ({status})'
        kept.append(hit)
    return kept


Search = Callable[[str], Awaitable[list[Hit]]]


def build_graph(search: Search, llm: LLMProvider) -> Any:  # noqa: ANN401 - a compiled LangGraph
    grade_chain = chain("assistant_grade", llm, Grade)
    rewrite_chain = chain("assistant_rewrite", llm, Rewrite)
    answer_chain = chain("assistant_answer", llm, Answer)

    async def retrieve(state: AskState) -> AskState:
        hits = await search(state["query"])
        return {"hits": hits, "trace": [f'Retrieved {len(hits)} document(s) for "{state["query"]}"']}

    async def grade(state: AskState) -> AskState:
        hits = state["hits"]
        if not hits:
            return {"relevant": [], "trace": ["Nothing to grade"]}
        verdict = await grade_chain.ainvoke({"question": state["question"], "documents": format_documents(hits)})
        relevant = [hits[n - 1] for n in _picked(verdict.relevant, hits)]
        return {"relevant": relevant, "trace": [f"Grader kept {len(relevant)} of {len(hits)} document(s)"]}

    def route(state: AskState) -> str:
        if state.get("relevant"):
            return "generate"
        if state.get("hits") and state.get("rewrites", 0) < MAX_REWRITES:
            return "rewrite"
        return "generate"

    async def rewrite(state: AskState) -> AskState:
        new = await rewrite_chain.ainvoke({"question": state["question"], "query": state["query"]})
        return {"query": new.query, "rewrites": state.get("rewrites", 0) + 1,
                "trace": [f'Nothing relevant: rewrote the search query to "{new.query}"']}

    async def generate(state: AskState) -> AskState:
        docs = state.get("relevant") or []
        if not docs:
            return {"answer": NOTHING_FOUND, "sources": [], "trace": ["No relevant history: answered without the LLM"]}
        reply = await answer_chain.ainvoke({"question": state["question"], "documents": format_documents(docs)})
        cited = _picked(reply.cited, docs) or list(range(1, len(docs) + 1))
        return {"answer": reply.answer.strip(), "sources": [(n, docs[n - 1]) for n in cited],
                "trace": [f"Answered from {len(docs)} document(s), citing {len(cited)}"]}

    graph = StateGraph(AskState)
    graph.add_node("retrieve", retrieve)
    graph.add_node("grade", grade)
    graph.add_node("rewrite", rewrite)
    graph.add_node("generate", generate)
    graph.add_edge(START, "retrieve")
    graph.add_edge("retrieve", "grade")
    graph.add_conditional_edges("grade", route, {"rewrite": "rewrite", "generate": "generate"})
    graph.add_edge("rewrite", "retrieve")
    graph.add_edge("generate", END)
    return graph.compile()


async def ask(kb: KnowledgeBase, llm: LLMProvider, project_id: PydanticObjectId, question: str) -> AskResult:
    async def search(query: str) -> list[Hit]:
        return await with_live_status(await kb.search(project_id, query, k=MAX_DOCUMENTS), project_id)

    calls_before = llm.usage.calls
    state = await build_graph(search, llm).ainvoke({"question": question, "query": question, "rewrites": 0, "trace": []})
    return AskResult(answer=state["answer"], sources=state.get("sources", []), trace=state.get("trace", []),
                     llm_calls=llm.usage.calls - calls_before)
