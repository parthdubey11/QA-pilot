"""RAG memory: vector store, retrieval for the Reporter and Healer, similar bugs, the LangGraph assistant and
reindexing. LLM always mocked; embeddings are the hashing ones from conftest (no model download)."""

import re
from datetime import timedelta

import httpx
import pytest
from beanie import PydanticObjectId
from langchain_core.documents import Document
from pymongo.asynchronous.database import AsyncDatabase

from app.core.config import get_settings
from app.models.accessibility import A11yIssue
from app.models.base import utcnow
from app.models.bug import Bug, title_key
from app.models.saved_test import HealEvent, Locators, SavedStep, SavedTest
from app.models.test_case import TestCase
from app.rag import KnowledgeBase, bug_document, heal_document, memory, remember, set_knowledge_base
from app.rag.reindex import reindex, reindex_if_empty
from app.routes import assistant as assistant_route
from tests.conftest import RegisterFn
from tests.test_agents import FakeLLM
from tests.test_bugs import execution, report
from worker.agents.assistant import NOTHING_FOUND, Answer, Grade, Rewrite, ask
from worker.agents.healer import HealDecision, find_replacement, past_repairs
from worker.agents.reporter import BugReport, report_bug
from worker.browser import Snapshot


async def add_bug(project_id: PydanticObjectId, title: str, actual: str, *, status: str = "open",
                  days_ago: int = 0, index: bool = True) -> Bug:
    seen = utcnow() - timedelta(days=days_ago)
    bug = Bug(project_id=project_id, title=title, title_key=title_key(title), severity="high", steps=["Open the page"],
              expected="It works", actual=actual, suggested_fix="Fix the handler", first_run_id=PydanticObjectId(),
              first_test_title="A test", status=status, first_seen_at=seen, last_seen_at=seen)  # type: ignore[arg-type]
    await bug.insert()
    if index:
        await remember(bug_document(bug))
    return bug


def finished_test(project_id: PydanticObjectId, title: str, reason: str) -> TestCase:
    return TestCase(run_id=PydanticObjectId(), project_id=project_id, index=0, title=title, type="happy",
                    steps=["Open the page"], expected="It works", status="passed", reason=reason)


def doc_number(prompt: str, text: str) -> int:
    """The [N] of the retrieved document containing `text` (what a real grader model would read)."""
    for match in re.finditer(r"^\[(\d+)\] \(.*?(?=^\[\d+\] \(|^## |\Z)", prompt, re.MULTILINE | re.DOTALL):
        if text in match.group(0):
            return int(match.group(1))
    raise AssertionError(f"{text!r} not among the documents:\n{prompt}")


def disable_rag(monkeypatch: pytest.MonkeyPatch) -> None:
    set_knowledge_base(None)
    monkeypatch.setattr(get_settings(), "rag_enabled", False)


# ---------- vector store ----------


async def test_store_filters_by_project_and_kind_upserts_and_deletes(knowledge: KnowledgeBase) -> None:
    p1, p2 = PydanticObjectId(), PydanticObjectId()

    def doc(project: PydanticObjectId, kind: str, ref: str, text: str) -> tuple[str, Document]:
        return f"{kind}:{ref}", Document(page_content=text, metadata={
            "kind": kind, "ref_id": ref, "project_id": str(project), "title": text})

    await knowledge.upsert([doc(p1, "bug", "a", "cart accepts a negative quantity"),
                            doc(p1, "bug", "b", "logout keeps the session alive"),
                            doc(p1, "test", "c", "cart quantity test passed"),
                            doc(p2, "bug", "d", "cart accepts a negative quantity")])

    hits = await knowledge.search(p1, "negative quantity in the cart", kinds=["bug"], k=5)
    assert [h.ref_id for h in hits] == ["a", "b"]  # only p1's bugs, most similar first
    assert hits[0].similarity > 0.5 > hits[1].similarity and hits[0].kind == "bug"
    assert {h.ref_id for h in await knowledge.search(p1, "cart quantity", k=5)} == {"a", "b", "c"}
    assert [h.ref_id for h in await knowledge.search(p1, "cart", kinds=["bug"], exclude_ids=("bug:a",))] == ["b"]
    assert await knowledge.search(p1, "cart", min_similarity=0.99) == []

    await knowledge.upsert([doc(p1, "bug", "a", "the checkout button is missing")])  # same id: replaced
    assert await knowledge.count(p1) == 3
    assert (await knowledge.search(p1, "checkout button", kinds=["bug"], k=1))[0].ref_id == "a"

    await knowledge.delete_project(p1)
    assert await knowledge.count(p1) == 0 and await knowledge.count(p2) == 1


async def test_remember_never_raises_when_the_store_fails(knowledge: KnowledgeBase,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    async def broken(_items: object) -> None:
        raise ConnectionError("chroma is down")

    monkeypatch.setattr(knowledge, "upsert", broken)
    assert await remember(("bug:x", Document(page_content="x", metadata={}))) is False


# ---------- Reporter: retrieval-augmented dedup ----------


async def test_reporter_finds_an_old_fixed_bug_beyond_the_recent_window(database: AsyncDatabase) -> None:
    project = PydanticObjectId()
    old = await add_bug(project, "Cart accepts a negative quantity",
                        "Quantity -1 was accepted and the cart total became negative", status="fixed", days_ago=90)
    for i in range(45):  # newer bugs: the old fallback (40 most recently seen) would not show the cart bug
        await add_bug(project, f"Footer link {i} has the wrong colour", f"Link {i} is grey instead of blue", days_ago=i % 9)
    llm = FakeLLM({BugReport: [lambda p: report(title="Negative quantities accepted in cart",
                                                duplicate_of=doc_number_in_list(p, "Cart accepts a negative quantity"))]})

    outcome = await report_bug(
        llm, project_id=project, run_id=PydanticObjectId(),
        test=TestCase.model_construct(index=0, title="Cart rejects negative quantities", type="edge",
                                      steps=["Set the quantity to -1"], expected="An error; the quantity stays 1"),
        verdict_reason="The cart accepted quantity -1 and showed a negative total",
        execution=execution("http://shop.test/cart"), screenshot_path=None,
    )

    prompt = llm.prompts(BugReport)[0]
    assert re.search(r"\[1\] \(fixed, high, \d+% similar\) Cart accepts a negative quantity", prompt)
    assert len(re.findall(r"^\[\d+\] \(", prompt, re.MULTILINE)) == get_settings().rag_bug_candidates
    assert "semantic search" in prompt
    assert outcome.bug.id == old.id and outcome.reopened and not outcome.created  # regression recognised
    assert outcome.retrieval == "semantic" and outcome.top_match is not None
    assert outcome.top_match[0] == "Cart accepts a negative quantity"


def doc_number_in_list(prompt: str, title: str) -> int:
    match = re.search(rf"^\[(\d+)\] \([^)]*\) {re.escape(title)}", prompt, re.MULTILINE)
    assert match, prompt
    return int(match.group(1))


async def test_new_bug_is_indexed_at_once_for_later_tests_in_the_run(database: AsyncDatabase,
                                                                     knowledge: KnowledgeBase) -> None:
    project = PydanticObjectId()
    kwargs = {"project_id": project, "run_id": PydanticObjectId(), "verdict_reason": "Second signup succeeded",
              "execution": execution(), "screenshot_path": None,
              "test": TestCase.model_construct(index=0, title="Duplicate signup", type="edge", steps=["Sign up twice"],
                                               expected="Rejected")}
    first = await report_bug(FakeLLM({BugReport: [report()]}), **kwargs)
    assert first.created and first.retrieval == "recent"  # nothing indexed yet
    assert (await knowledge.search(project, "duplicate email signup", kinds=["bug"]))[0].ref_id == str(first.bug.id)

    llm = FakeLLM({BugReport: [report(title="Same email registers twice", duplicate_of=1)]})
    second = await report_bug(llm, **kwargs)
    assert not second.created and second.bug.id == first.bug.id and second.retrieval == "semantic"


async def test_reporter_falls_back_to_recent_bugs_without_the_store(database: AsyncDatabase,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    disable_rag(monkeypatch)
    project = PydanticObjectId()
    await add_bug(project, "Logout keeps the session", "Still logged in", index=False)
    llm = FakeLLM({BugReport: [report()]})
    outcome = await report_bug(llm, project_id=project, run_id=PydanticObjectId(), verdict_reason="x",
                               test=TestCase.model_construct(index=0, title="t", type="edge", steps=["s"], expected="e"),
                               execution=execution(), screenshot_path=None)
    assert "[1] (open, high) Logout keeps the session" in llm.prompts(BugReport)[0]
    assert outcome.created and outcome.retrieval == "recent"


# ---------- Healer: earlier repairs ----------


async def test_healer_prompt_includes_the_most_similar_earlier_repairs() -> None:
    project = PydanticObjectId()
    saved = SavedTest.model_construct(id=PydanticObjectId(), project_id=project, title="Sign up with valid details")
    renamed = HealEvent(run_id=PydanticObjectId(), step_index=2, method="ai-healer",
                        old=Locators(role="button", name="Sign Up"), new=Locators(role="button", name="Create Account"),
                        reason="the signup button was renamed")
    other = HealEvent(run_id=PydanticObjectId(), step_index=0, method="fallback-locator",
                      old=Locators(role="link", name="Contact"), new=Locators(role="link", name="Contact us"),
                      reason="found by css")
    await remember(heal_document(saved, renamed), heal_document(saved, other))
    step = SavedStep(tool="click", locators=Locators(role="button", name="Sign Up", tag="button"),
                     note="submit the signup form")

    repairs = await past_repairs(project, step)
    assert repairs and 'button "Create Account"' in repairs[0].text
    assert await past_repairs(PydanticObjectId(), step) == []  # other projects' repairs are never used

    llm = FakeLLM({HealDecision: [HealDecision(ref=None, reason="no match")]})
    await find_replacement(llm, test_title="Checkout", step=step, previous=[], repairs=repairs,
                           snapshot=Snapshot(url="http://shop.test/", title="Shop", elements=[]), snapshot_text="")
    prompt = llm.prompts(HealDecision)[0]
    assert "## Earlier repairs on this site" in prompt and 'replaced it with button "Create Account"' in prompt


# ---------- assistant (LangGraph corrective RAG) ----------


@pytest.fixture
async def history(database: AsyncDatabase) -> tuple[PydanticObjectId, Bug]:
    project = PydanticObjectId()
    bug = await add_bug(project, "Cart accepts a negative quantity", "Quantity -1 accepted; total became -$10",
                        status="fixed")
    await add_bug(project, "Logout does not end the session", "After logging out, /account still shows the user")
    await remember(memory.test_document(finished_test(project, "Contact form sends a message", "Thanks message shown")))
    return project, bug


async def test_assistant_retrieves_grades_and_answers_with_citations(history: tuple[PydanticObjectId, Bug],
                                                                    knowledge: KnowledgeBase) -> None:
    project, bug = history
    llm = FakeLLM({
        Grade: [lambda p: Grade(relevant=[doc_number(p, "Cart accepts a negative quantity")])],
        Answer: [Answer(answer="Yes: the cart accepted a quantity of -1 [1]. It is marked fixed.", cited=[1])],
    })

    result = await ask(knowledge, llm, project, "Did we have problems with negative quantities in the cart?")

    assert result.answer.startswith("Yes: the cart accepted") and result.llm_calls == 2
    assert [(n, hit.ref_id) for n, hit in result.sources] == [(1, str(bug.id))]
    grade_prompt = llm.prompts(Grade)[0]
    assert "status fixed, seen 1×" in grade_prompt  # live status from MongoDB, not the stored text
    assert "Did we have problems with negative quantities" in grade_prompt
    answer_prompt = llm.prompts(Answer)[0]
    assert "Logout" not in answer_prompt and "[1] (bug report; status fixed" in answer_prompt  # only graded docs
    assert result.trace[0].startswith("Retrieved 3 document(s)") and "Grader kept 1 of 3" in result.trace[1]


async def test_assistant_tells_the_llm_which_bug_a_failed_test_became(history: tuple[PydanticObjectId, Bug],
                                                                      knowledge: KnowledgeBase) -> None:
    project, bug = history
    failed = finished_test(project, "Cart rejects negative quantities", "Quantity -1 was accepted")
    failed.status, failed.bug_id = "failed", bug.id
    await remember(memory.test_document(failed))
    llm = FakeLLM({Grade: [Grade(relevant=[])], Rewrite: [Rewrite(query="cart quantity")]})

    await ask(knowledge, llm, project, "Is the negative quantity problem in the cart fixed?")

    assert 'reported as bug "Cart accepts a negative quantity" (status fixed, seen 1×' in llm.prompts(Grade)[0]


async def test_assistant_rewrites_the_query_once_when_nothing_is_relevant(history: tuple[PydanticObjectId, Bug],
                                                                         knowledge: KnowledgeBase) -> None:
    project, _ = history
    llm = FakeLLM({
        Grade: [Grade(relevant=[]), lambda p: Grade(relevant=[doc_number(p, "Logout does not end the session")])],
        Rewrite: [Rewrite(query="logout session still logged in")],
        Answer: [Answer(answer="Logging out does not end the session [1].", cited=[1])],
    })

    result = await ask(knowledge, llm, project, "can people stay signed in after they leave?")

    assert result.llm_calls == 4 and "[1]" in result.answer
    assert 'rewrote the search query to "logout session still logged in"' in result.trace[2]
    assert result.trace[3].startswith('Retrieved 3 document(s) for "logout session still logged in"')
    assert result.sources[0][1].title == "Logout does not end the session"


async def test_assistant_says_so_when_history_has_nothing(history: tuple[PydanticObjectId, Bug],
                                                         knowledge: KnowledgeBase) -> None:
    project, _ = history
    llm = FakeLLM({Grade: [Grade(relevant=[])], Rewrite: [Rewrite(query="payment refund")]})
    result = await ask(knowledge, llm, project, "Were refunds tested?")
    assert result.answer == NOTHING_FOUND and result.sources == [] and result.llm_calls == 3  # grade, rewrite, grade

    empty = await ask(knowledge, FakeLLM({}), PydanticObjectId(), "Anything?")  # no history: no LLM call at all
    assert empty.answer == NOTHING_FOUND and empty.llm_calls == 0


# ---------- API ----------


async def new_project(client: httpx.AsyncClient, headers: dict[str, str]) -> str:
    res = await client.post("/projects", json={"name": "Shop", "base_url": "http://shop.test",
                                               "authorised_testing_confirmed": True}, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def test_ask_api_answers_saves_history_and_is_private(client: httpx.AsyncClient, register: RegisterFn,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    headers = await register()
    pid = await new_project(client, headers)
    bug = await add_bug(PydanticObjectId(pid), "Cart accepts a negative quantity", "Quantity -1 accepted")
    llm = FakeLLM({Grade: [Grade(relevant=[1])], Answer: [Answer(answer="The cart accepts -1 [1].", cited=[1])]})
    monkeypatch.setattr(assistant_route, "get_provider", lambda: llm)

    res = await client.post(f"/projects/{pid}/assistant", json={"question": "Any cart bugs?"}, headers=headers)
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["answer"] == "The cart accepts -1 [1]." and body["llm_calls"] == 2
    assert body["sources"][0] | {"similarity": 0, "excerpt": ""} == {
        "n": 1, "kind": "bug", "ref_id": str(bug.id), "title": "Cart accepts a negative quantity",
        "run_id": str(bug.first_run_id), "similarity": 0, "excerpt": ""}
    history = (await client.get(f"/projects/{pid}/assistant", headers=headers)).json()
    assert [h["question"] for h in history] == ["Any cart bugs?"]

    other = await register(email="bob@example.com")
    assert (await client.post(f"/projects/{pid}/assistant", json={"question": "Any bugs?"}, headers=other)).status_code == 404
    assert (await client.get(f"/projects/{pid}/assistant", headers=other)).status_code == 404
    assert (await client.post(f"/projects/{pid}/assistant", json={"question": "?"}, headers=headers)).status_code == 422

    monkeypatch.setattr(get_settings(), "max_assistant_questions_per_user_per_day", 1)
    res = await client.post(f"/projects/{pid}/assistant", json={"question": "Any cart bugs?"}, headers=headers)
    assert res.status_code == 429 and "today's 1 questions" in res.json()["detail"]


async def test_ask_api_without_the_store_is_unavailable(client: httpx.AsyncClient, register: RegisterFn,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    headers = await register()
    pid = await new_project(client, headers)
    disable_rag(monkeypatch)
    res = await client.post(f"/projects/{pid}/assistant", json={"question": "Any cart bugs?"}, headers=headers)
    assert res.status_code == 503 and "knowledge base" in res.json()["detail"]


async def test_similar_bugs_endpoint(client: httpx.AsyncClient, register: RegisterFn) -> None:
    headers = await register()
    pid = PydanticObjectId(await new_project(client, headers))
    bug = await add_bug(pid, "Cart accepts a negative quantity", "Quantity -1 was accepted in the cart")
    twin = await add_bug(pid, "Negative quantity allowed in cart", "The cart accepted quantity -3", status="fixed")
    await add_bug(pid, "Logout does not end the session", "After logout the account page still loads")
    await add_bug(PydanticObjectId(), "Cart accepts a negative quantity", "Quantity -1 accepted")  # another project

    similar = (await client.get(f"/bugs/{bug.id}/similar", headers=headers)).json()
    assert similar[0]["id"] == str(twin.id) and similar[0]["status"] == "fixed" and 0 < similar[0]["similarity"] <= 1
    assert str(bug.id) not in {s["id"] for s in similar} and len(similar) <= 2

    other = await register(email="bob@example.com")
    assert (await client.get(f"/bugs/{bug.id}/similar", headers=other)).status_code == 404


async def test_deleting_a_project_forgets_its_vectors(client: httpx.AsyncClient, register: RegisterFn,
                                                      knowledge: KnowledgeBase) -> None:
    headers = await register()
    pid = await new_project(client, headers)
    await add_bug(PydanticObjectId(pid), "Cart accepts a negative quantity", "Quantity -1 accepted")
    assert await knowledge.count(pid) == 1
    assert (await client.delete(f"/projects/{pid}", headers=headers)).status_code == 204
    assert await knowledge.count(pid) == 0


# ---------- reindexing ----------


async def test_reindex_rebuilds_the_store_from_mongodb(database: AsyncDatabase, knowledge: KnowledgeBase) -> None:
    project, run = PydanticObjectId(), PydanticObjectId()
    await add_bug(project, "Cart accepts a negative quantity", "Quantity -1 accepted", index=False)
    await finished_test(project, "Contact form sends a message", "Thanks message shown").insert()
    await TestCase(run_id=run, project_id=project, index=1, title="Still pending", type="happy", steps=["x"],
                   expected="y").insert()  # not finished: not indexed
    for run_id in (PydanticObjectId(), run):  # the same rule on the same page in two runs: indexed once
        await A11yIssue(project_id=project, run_id=run_id, page_path="/", page_url="http://shop.test/",
                        rule_id="image-alt", source="axe", impact="critical", title="Images must have alternate text",
                        description="2 images have no alt", how_to_fix="Add alt text", wcag=["1.1.1"]).insert()
    await SavedTest(project_id=project, title="Sign up", title_key="sign up", start_path="/signup", steps=[],
                    source_run_id=run, heal_history=[HealEvent(
                        run_id=run, step_index=0, method="ai-healer", old=Locators(role="button", name="Sign Up"),
                        new=Locators(role="button", name="Register"), reason="renamed")]).insert()

    assert await reindex_if_empty() == {"bug": 1, "test": 1, "a11y": 1, "heal": 1}
    assert await knowledge.count(project) == 4
    hits = await knowledge.search(project, "image alt text missing", kinds=["a11y"])
    assert len(hits) == 1 and hits[0].metadata["run_id"] == str(run)  # the latest run's finding
    assert await reindex_if_empty() is None  # not empty any more
    assert await reindex(knowledge, project) == {"bug": 1, "test": 1, "a11y": 1, "heal": 1}  # idempotent upserts
    assert await knowledge.count(project) == 4
