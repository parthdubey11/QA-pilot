"""Accessibility audit: axe-core, keyboard walk (+LLM review), alt-text vision check, scoring. LLM mocked."""

import base64
from collections.abc import AsyncIterator

import pytest
from playwright.async_api import Browser, Page

from tests.test_agents import FakeLLM, browser  # noqa: F401 - fixture
from worker.audits.accessibility import (
    A11yFinding,
    AltReview,
    KeyboardReview,
    audit_page,
    keyboard_walk,
    page_template,
    score_findings,
)
from worker.llm import LLMError, LLMOutputError

PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAIAAAAlC+aJAAAAQ0lEQVR42u3OMQ0AAAjAMPybBhkcS6vQJpM6WgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgLgaxT+AV8JqdV9AAAAAElFTkSuQmCC"
)
IMG = "data:image/png;base64," + base64.b64encode(PNG_1X1).decode()

BAD_PAGE = f"""<!doctype html><html><head><title>Shop</title>
<style> .ghost {{ color: #ccc; }} .btn:focus, .btn:focus-visible {{ outline: none; }} </style></head><body>
<nav><a href="#home">Home</a> <a href="#cart"><svg aria-hidden="true" width="10" height="10"></svg></a></nav>
<main><h1>Products</h1>
<p class="ghost">Pale text that is hard to read.</p>
<img src="{IMG}" width="64" height="64">
<img src="{IMG}" alt="photo" width="64" height="64">
<img src="{IMG}" alt="Blue square logo" width="64" height="64">
<input id="q" placeholder="Search">
<button class="btn">Add to cart</button>
<div onclick="alert(1)" class="fake">Fake button</div>
<label for="promo">Promo</label><input id="promo" onkeydown="if (event.key === 'Tab') event.preventDefault()">
<button>After the trap</button>
</main></body></html>"""

GOOD_PAGE = """<!doctype html><html lang="en"><head><title>Fine</title></head><body><main><h1>All good</h1>
<label for="e">Email</label><input id="e" type="email"><button>Save</button><a href="#more">More</a>
<fieldset><legend>Size</legend><label><input type="radio" name="s"> S</label><label><input type="radio" name="s"> M</label></fieldset>
</main></body></html>"""


@pytest.fixture
async def page(browser: Browser) -> AsyncIterator[Page]:  # noqa: F811
    context = await browser.new_context()
    pg = await context.new_page()
    yield pg
    await context.close()


def review_all(prompt: str) -> KeyboardReview:
    """A reviewer that confirms every candidate (what a real model does for real problems)."""
    findings = []
    if "Focus got stuck" in prompt:
        findings.append({"kind": "keyboard-trap", "title": "Promo field traps keyboard focus", "impact": "critical",
                         "description": "Tab never leaves the promo field.", "elements": ['textbox "Promo" (<input>)'],
                         "how_to_fix": "Remove preventDefault on Tab."})
    if "focus visible: no" in prompt:
        findings.append({"kind": "focus-not-visible", "title": "Add to cart has no focus ring", "impact": "serious",
                         "description": "No outline.", "elements": ['button "Add to cart" (<button>)'],
                         "how_to_fix": "Add :focus-visible outline."})
    return KeyboardReview.model_validate({"findings": findings})


# ---------- pieces ----------


def test_score_and_templates() -> None:
    def finding(impact: str, nodes: int) -> A11yFinding:
        return A11yFinding(rule_id="r", source="axe", impact=impact, title="t", description="d", how_to_fix="f",
                           nodes=[{"target": f"#{i}"} for i in range(nodes)])

    assert score_findings([]) == 100
    assert score_findings([finding("critical", 1), finding("minor", 1)]) == 83
    assert score_findings([finding("serious", 9)]) == 86  # +1 per extra element, capped at +4
    assert score_findings([finding("critical", 10)] * 10) == 0
    assert page_template("/products/12") == "/products/:id"
    assert page_template("/products/12/reviews?x=1") == "/products/:id/reviews"
    assert page_template("/runs/66f7a1c2e4b0a1b2c3d4e5f6") == "/runs/:id"
    assert page_template("/signup") == "/signup"


async def test_keyboard_walk_finds_trap_invisible_focus_and_unreachable(page: Page) -> None:
    await page.set_content(BAD_PAGE)

    walk = await keyboard_walk(page, max_tabs=30)

    names = [s.name for s in walk.stops]
    assert names[:4] == ["Home", "", "Search", "Add to cart"]
    assert walk.trap is not None and walk.trap.name == "Promo"
    assert "After the trap" not in names  # never reached because of the trap
    add = next(s for s in walk.stops if s.name == "Add to cart")
    home = next(s for s in walk.stops if s.name == "Home")
    assert add.visible_focus is False and home.visible_focus is True
    assert walk.unreachable == []  # not reported when a trap cuts the walk short
    assert await page.locator("[data-qa-kb-start]").count() == 0  # helper removed again


async def test_keyboard_walk_reports_unreachable_controls_and_is_clean_on_a_good_page(page: Page) -> None:
    await page.set_content(BAD_PAGE.replace("onkeydown=", "data-x="))
    walk = await keyboard_walk(page)
    assert walk.trap is None and walk.complete
    assert [s.name for s in walk.unreachable] == ["Fake button"]

    await page.set_content(GOOD_PAGE)
    good = await keyboard_walk(page)
    assert good.trap is None and good.unreachable == []  # one radio of the group in tab order is fine
    assert all(s.visible_focus for s in good.stops)


# ---------- whole page ----------


async def test_audit_page_combines_axe_keyboard_and_vision(page: Page) -> None:
    await page.set_content(BAD_PAGE)
    llm = FakeLLM({
        AltReview: [AltReview.model_validate({"results": [
            {"index": 1, "verdict": "poor", "reason": "'photo' says nothing", "suggested_alt": "Blue square"},
            {"index": 2, "verdict": "good", "reason": "ok"},
        ]})],
        KeyboardReview: [review_all],
    })

    result = await audit_page(page, llm, max_tabs=30)

    rules = {f.rule_id: f for f in result.findings}
    assert {"html-has-lang", "image-alt", "color-contrast", "link-name"} <= set(rules)  # axe (a placeholder counts as a name)
    assert rules["image-alt"].wcag == ["1.1.1"] and rules["image-alt"].help_url
    assert rules["html-has-lang"].how_to_fix.startswith('Add the page language')
    assert rules["keyboard-trap"].impact == "critical" and rules["keyboard-trap"].wcag == ["2.1.2"]
    assert rules["focus-not-visible"].nodes[0].target == 'button "Add to cart" (<button>)'
    alt = rules["alt-text-quality"]
    assert alt.source == "vision" and len(alt.nodes) == 1 and 'Suggested alt: "Blue square"' in alt.nodes[0].summary
    schema, prompt, images = next(c for c in llm.calls if c[0] is AltReview)
    assert len(images) == 2 and images[0].startswith(b"\x89PNG")  # cropped images sent to the vision model
    assert 'Image 1: alt="photo"' in prompt and 'Image 2: alt="Blue square logo"' in prompt
    keyboard_prompt = next(p for s, p, _ in llm.calls if s is KeyboardReview)
    assert "Focus got stuck on textbox \"Promo\"" in keyboard_prompt
    assert result.score == score_findings(result.findings) and result.score < 50
    assert result.llm_used


async def test_clean_page_needs_no_llm_call(page: Page) -> None:
    await page.set_content(GOOD_PAGE)
    llm = FakeLLM({})

    result = await audit_page(page, llm)

    assert result.findings == [] and result.score == 100 and llm.calls == []


async def test_without_llm_the_raw_keyboard_findings_are_reported(page: Page) -> None:
    await page.set_content(BAD_PAGE.replace("onkeydown=", "data-x="))

    result = await audit_page(page, None)

    rules = {f.rule_id for f in result.findings}
    assert {"focus-not-visible", "unreachable-control"} <= rules and "alt-text-quality" not in rules


async def test_llm_quota_falls_back_to_raw_findings(page: Page) -> None:
    await page.set_content(BAD_PAGE)
    llm = FakeLLM({AltReview: [LLMError("groq: the daily request quota is used up")]})

    result = await audit_page(page, llm, max_tabs=30)

    assert result.llm_unavailable == "groq: the daily request quota is used up"
    assert "keyboard-trap" in {f.rule_id for f in result.findings}  # still reported, from the Tab walk
    assert [s for s, _, _ in llm.calls] == [AltReview]  # no further LLM calls after the quota error


async def test_bad_llm_output_for_keyboard_review_uses_raw_findings(page: Page) -> None:
    await page.set_content(BAD_PAGE.replace('alt="photo" ', "").replace('alt="Blue square logo" ', ""))
    llm = FakeLLM({KeyboardReview: [LLMOutputError("invalid JSON twice", raw="?")]})

    result = await audit_page(page, llm, max_tabs=30)

    assert result.llm_unavailable is None and "keyboard-trap" in {f.rule_id for f in result.findings}


# ---------- in a run ----------

import httpx  # noqa: E402

from app.models.accessibility import A11yAudit, A11yIssue  # noqa: E402
from tests.conftest import RegisterFn  # noqa: E402
from tests.test_agents import FakeSiteLauncher, decide, launcher, launcher_factory, storage  # noqa: E402, F401
from worker import main as worker  # noqa: E402
from worker.agents.executor import ExecutorDecision  # noqa: E402
from worker.agents.judge import Verdict  # noqa: E402
from worker.agents.planner import TestPlan  # noqa: E402
from worker.orchestrator import handle_run_job  # noqa: E402


async def test_run_with_accessibility_option_audits_each_page_template(
    client: httpx.AsyncClient, register: RegisterFn, launcher: FakeSiteLauncher, storage,  # noqa: ANN001, F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests import test_agents

    monkeypatch.setitem(test_agents.PAGES, "/", test_agents.PAGES["/"] + "<img src='/banner.png' width=80 height=40>")
    headers = await register()
    pid = (await client.post("/projects", json={"name": "Shop", "base_url": "http://shop.test",
                                                 "authorised_testing_confirmed": True}, headers=headers)).json()["id"]
    run_id = (await client.post(f"/projects/{pid}/runs", json={"goal": "check it", "options": {
        "max_tests": 1, "accessibility": True}}, headers=headers)).json()["id"]
    llm = FakeLLM({
        TestPlan: [TestPlan.model_validate({"test_cases": [{"title": "Home", "type": "happy", "start_path": "/",
                                                            "steps": ["Open"], "expected": "Products"}]})],
        ExecutorDecision: [decide("ok", tool="done", result="pass", reason="Products shown")],
        Verdict: [Verdict(result="pass", reason="ok")],
        KeyboardReview: [review_all],
        AltReview: [AltReview()],
    })
    job = await worker.claim_next_job()
    assert job is not None

    await handle_run_job(job, launcher_factory=launcher_factory(launcher), llm=llm)

    audit = await A11yAudit.find_one()
    assert audit is not None and str(audit.run_id) == run_id
    paths = [p.path for p in audit.pages]
    assert "/" in paths and "/signup" in paths and "/products/1" in paths
    assert audit.issue_count == await A11yIssue.count() == 1 and audit.score < 100
    issue = await A11yIssue.find_one()
    assert issue is not None and (issue.page_path, issue.rule_id, issue.source) == ("/", "image-alt", "axe")
    assert issue.how_to_fix.startswith("Give every <img> an alt attribute")
    home = next(p for p in audit.pages if p.path == "/")
    assert home.score == 85 and home.issues == 1
    assert next(p for p in audit.pages if p.path == "/signup").score == 100
    steps = (await client.get(f"/runs/{run_id}/steps", headers=headers)).json()
    audit_steps = [s for s in steps if s["phase"] == "audit"]
    assert audit_steps[-1]["message"].startswith(f"Accessibility score: {audit.score}/100")
    assert any(s["has_screenshot"] for s in audit_steps)
    run = (await client.get(f"/runs/{run_id}", headers=headers)).json()
    assert run["status"] == "completed" and run["stats"]["a11y_score"] == audit.score
    phases = [s["phase"] for s in steps if s["phase"]]
    assert phases.index("audit") < phases.index("plan")  # audit right after exploring
    html = (await client.get(f"/runs/{run_id}/report.html", headers=headers)).text
    assert f"Accessibility — {audit.score}/100" in html and "Give every &lt;img&gt; an alt attribute" in html


async def test_accessibility_api(client: httpx.AsyncClient, register: RegisterFn) -> None:
    from datetime import timedelta

    from beanie import PydanticObjectId

    from app.models.accessibility import A11yPageScore
    from app.models.base import utcnow

    alice = await register(email="alice@example.com")
    bob = await register(email="bob@example.com")
    pid = (await client.post("/projects", json={"name": "Shop", "base_url": "http://shop.test",
                                                 "authorised_testing_confirmed": True}, headers=alice)).json()["id"]
    assert (await client.get(f"/projects/{pid}/accessibility", headers=alice)).json() == {
        "audit": None, "issues": [], "history": []}

    old_run, new_run = PydanticObjectId(), PydanticObjectId()
    for run, score, age in ((old_run, 60, 2), (new_run, 81, 1)):
        await A11yAudit(project_id=PydanticObjectId(pid), run_id=run, score=score, issue_count=2,
                        pages=[A11yPageScore(path="/", url="http://shop.test/", title="Home", score=score, issues=2)],
                        created_at=utcnow() - timedelta(hours=age)).insert()
        for rule, impact in (("color-contrast", "serious"), ("keyboard-trap", "critical")):
            await A11yIssue(project_id=PydanticObjectId(pid), run_id=run, page_path="/", page_url="http://shop.test/",
                            rule_id=rule, source="axe", impact=impact, title=rule, description="d", how_to_fix="fix",
                            wcag=["1.4.3"]).insert()

    latest = (await client.get(f"/projects/{pid}/accessibility", headers=alice)).json()
    assert latest["audit"]["score"] == 81 and latest["audit"]["run_id"] == str(new_run)
    assert [i["rule_id"] for i in latest["issues"]] == ["keyboard-trap", "color-contrast"]  # most severe first
    assert [h["score"] for h in latest["history"]] == [60, 81]  # oldest first, for a trend
    older = (await client.get(f"/projects/{pid}/accessibility?run_id={old_run}", headers=alice)).json()
    assert older["audit"]["score"] == 60 and len(older["issues"]) == 2
    assert (await client.get(f"/projects/{pid}/accessibility?run_id={PydanticObjectId()}", headers=alice)).status_code == 404
    assert (await client.get(f"/projects/{pid}/accessibility", headers=bob)).status_code == 404
    assert (await client.get(f"/projects/{pid}/accessibility")).status_code == 401

    assert (await client.delete(f"/projects/{pid}", headers=alice)).status_code == 204
    assert await A11yAudit.count() == 0 and await A11yIssue.count() == 0
