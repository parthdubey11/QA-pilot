"""Accessibility audit of one page: axe-core rules + keyboard-only navigation + alt-text quality (vision).

- axe-core (bundled, WCAG 2.x A/AA tags) finds the machine-checkable violations.
- The keyboard check really presses Tab through the page and records the focus order, whether each focused
  element visibly changes (focus indicator), keyboard traps and interactive elements never reached. When it
  finds candidate problems, the LLM reviews them and writes titles and fixes (no LLM call on clean pages).
- The alt-text check crops each image that has alt text and asks a vision model whether the text fits.
Each page gets a WCAG score out of 100 (see `score_findings`).
"""

import re
from pathlib import Path
from typing import Literal

from playwright.async_api import Page
from pydantic import BaseModel, Field

from app.models.accessibility import Impact, IssueNode, IssueSource
from worker.agents import render_prompt
from worker.llm import LLMError, LLMOutputError, LLMProvider

AXE_SOURCE = (Path(__file__).parent / "vendor" / "axe.min.js").read_text(encoding="utf-8")  # axe-core 4.13 (MPL-2.0)
WCAG_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]
IMPACT_WEIGHT: dict[str, int] = {"critical": 15, "serious": 10, "moderate": 5, "minor": 2}
MAX_IMAGES_PER_PAGE = 5

# Short, practical fixes for the most common axe rules (axe's own help text is used for the rest).
HOW_TO_FIX: dict[str, str] = {
    "image-alt": 'Give every <img> an alt attribute: describe what the image shows or is for, or use alt="" if it is purely decorative.',
    "input-image-alt": "Add alt text to <input type=\"image\"> describing the button's action (e.g. alt=\"Search\").",
    "label": 'Associate a visible label with the field: <label for="field-id">Email</label> (or wrap the input in the <label>). Placeholders are not labels.',
    "select-name": "Give the <select> a <label for> (or aria-label if a visible label is impossible).",
    "button-name": 'Give the button a text name: visible text, or aria-label="Remove item" for icon-only buttons (keep the SVG aria-hidden).',
    "link-name": 'Give the link a text name: visible text, visually hidden text, or aria-label (e.g. aria-label="Cart, 2 items") for icon links.',
    "color-contrast": "Increase the contrast between text and background to at least 4.5:1 (3:1 for large text), e.g. darken the text colour.",
    "html-has-lang": 'Add the page language to the root element: <html lang="en">.',
    "html-lang-valid": 'Use a valid language code in <html lang>, e.g. "en" or "hi".',
    "document-title": "Add a descriptive <title> to the page, e.g. \"Log in | Demo Shop\".",
    "meta-viewport": 'Do not block zoom: remove user-scalable=no and maximum-scale=1 from <meta name="viewport">.',
    "frame-title": "Give each <iframe> a title that describes its content.",
    "aria-required-attr": "Add the ARIA attributes this role requires (e.g. aria-checked on role=\"checkbox\").",
    "aria-valid-attr-value": "Fix the ARIA attribute value so it matches an allowed value or an existing element id.",
    "aria-hidden-focus": 'Elements inside aria-hidden="true" must not be focusable: remove them from the tab order (tabindex="-1") or unhide them.',
    "nested-interactive": "Don't put interactive elements (links, buttons) inside other interactive elements.",
    "list": "Only put <li> elements (or script/template) directly inside <ul>/<ol>.",
    "listitem": "Put <li> elements inside a <ul> or <ol>.",
    "autocomplete-valid": "Use a valid autocomplete token (e.g. email, current-password, street-address).",
    "target-size": "Make touch targets at least 24×24 CSS pixels, or space them apart.",
    "link-in-text-block": "Make links in text distinguishable without colour alone (underline them).",
    "duplicate-id-aria": "Make ids referenced by ARIA/labels unique on the page.",
}

KEYBOARD_WCAG = {"keyboard-trap": ["2.1.2"], "focus-not-visible": ["2.4.7"], "unreachable-control": ["2.1.1"],
                 "focus-order": ["2.4.3"]}


class A11yFinding(BaseModel):
    rule_id: str
    source: IssueSource
    impact: Impact
    title: str
    description: str
    how_to_fix: str
    wcag: list[str] = Field(default_factory=list)
    help_url: str | None = None
    nodes: list[IssueNode] = Field(default_factory=list)


class PageAuditResult(BaseModel):
    url: str
    title: str
    score: int
    findings: list[A11yFinding]
    focus_stops: int = 0
    llm_used: bool = False
    notes: list[str] = Field(default_factory=list)  # problems running a check (not accessibility issues)
    llm_unavailable: str | None = None  # set when the LLM failed (quota…): caller may stop using it


def score_findings(findings: list[A11yFinding]) -> int:
    """100 minus a deduction per violated rule: critical 15, serious 10, moderate 5, minor 2,
    plus 1 per additional affected element (max +4). Never below 0."""
    deduction = sum(IMPACT_WEIGHT[f.impact] + min(max(len(f.nodes) - 1, 0), 4) for f in findings)
    return max(0, 100 - deduction)


def page_template(path: str) -> str:
    """/products/12/reviews -> /products/:id/reviews, so similar pages are audited once."""
    path = path.split("?")[0] or "/"
    return re.sub(r"/(\d+|[0-9a-f]{24}|[0-9a-f-]{36})(?=/|$)", "/:id", path)


# ---------- axe-core ----------

AXE_RUN_JS = """
async (tags) => {
  const result = await axe.run(document, { runOnly: { type: 'tag', values: tags }, resultTypes: ['violations'] })
  return result.violations.map((v) => ({
    id: v.id, impact: v.impact || 'moderate', help: v.help, description: v.description, helpUrl: v.helpUrl, tags: v.tags,
    nodes: v.nodes.slice(0, 10).map((n) => ({
      target: n.target.map((t) => (Array.isArray(t) ? t.join(' ') : t)).join(' '),
      html: n.html.slice(0, 200),
      summary: (n.failureSummary || '').replace(/\\s+/g, ' ').slice(0, 300),
    })),
  }))
}
"""


def wcag_criteria(tags: list[str]) -> list[str]:
    return [f"{m[1]}.{m[2]}.{m[3]}" for t in tags if (m := re.fullmatch(r"wcag(\d)(\d)(\d+)", t))]


async def run_axe(page: Page) -> list[A11yFinding]:
    if not await page.evaluate("() => typeof window.axe !== 'undefined'"):
        await page.add_script_tag(content=AXE_SOURCE)
    violations = await page.evaluate(AXE_RUN_JS, WCAG_TAGS)
    return [
        A11yFinding(
            rule_id=v["id"],
            source="axe",
            impact=v["impact"] if v["impact"] in IMPACT_WEIGHT else "moderate",
            title=v["help"],
            description=v["description"],
            how_to_fix=HOW_TO_FIX.get(v["id"], v["help"] + "."),
            wcag=wcag_criteria(v["tags"]),
            help_url=v["helpUrl"],
            nodes=[IssueNode(**n) for n in v["nodes"]],
        )
        for v in violations
    ]


# ---------- keyboard-only navigation ----------

KB_PREPARE_JS = """
() => {
  const SEL = 'a[href], area[href], button, input:not([type=hidden]), select, textarea, summary, iframe, [tabindex], ' +
    '[contenteditable="true"], [role=button], [role=link], [role=checkbox], [role=switch], [role=tab], [role=menuitem], [onclick]'
  const shown = (el) => {
    const s = getComputedStyle(el)
    if (s.visibility === 'hidden' || s.display === 'none') return false
    const r = el.getBoundingClientRect()
    return r.width > 0 && r.height > 0
  }
  const clean = (t) => (t || '').replace(/\\s+/g, ' ').trim().slice(0, 60)
  const nameOf = (el) => clean(el.getAttribute('aria-label') || (el.labels && el.labels[0] && el.labels[0].innerText) ||
    el.innerText || el.value || el.getAttribute('placeholder') || el.getAttribute('title') || el.getAttribute('alt'))
  const roleOf = (el) => el.getAttribute('role') || ({ A: 'link', BUTTON: 'button', SELECT: 'combobox', TEXTAREA: 'textbox',
    SUMMARY: 'button', IFRAME: 'iframe' })[el.tagName] || (el.tagName === 'INPUT'
    ? ({ checkbox: 'checkbox', radio: 'radio', submit: 'button', button: 'button', reset: 'button', image: 'button' })[el.type] || 'textbox'
    : el.tagName.toLowerCase())
  const styleOf = (el) => { const s = getComputedStyle(el)
    return [s.outlineStyle, s.outlineWidth, s.outlineColor, s.boxShadow, s.borderColor, s.backgroundColor, s.color,
            s.textDecorationLine].join('|') }
  window.__qaKb = { nameOf, roleOf, styleOf, items: [] }

  document.querySelectorAll('[data-qa-kb]').forEach((e) => e.removeAttribute('data-qa-kb'))
  if (document.activeElement && document.activeElement.blur) document.activeElement.blur()
  for (const el of document.querySelectorAll(SEL)) {
    if (!shown(el) || el.disabled || el.closest('[inert], [aria-hidden="true"]')) continue
    const native = el.matches('a[href], area[href], button, input, select, textarea, summary')
    if (el.getAttribute('tabindex') === '-1' && !native && !el.matches('[onclick], [role=button], [role=link]')) continue
    const id = String(window.__qaKb.items.length)
    el.setAttribute('data-qa-kb', id)
    window.__qaKb.items.push({ id, role: roleOf(el), name: nameOf(el), tag: el.tagName.toLowerCase(),
      group: el.type === 'radio' ? el.name : '', baseline: styleOf(el) })
  }
  // A focusable starting point at the very top of the page, so the first Tab reaches the first element.
  const start = document.createElement('span')
  start.tabIndex = 0
  start.setAttribute('data-qa-kb-start', '')
  document.body.prepend(start)
  start.focus()
  return window.__qaKb.items.map(({ baseline, ...rest }) => rest)
}
"""

KB_CURRENT_JS = """
() => {
  const el = document.activeElement
  if (!el || el === document.body || el === document.documentElement) return null
  if (el.hasAttribute('data-qa-kb-start')) return { start: true }
  const kb = window.__qaKb
  const id = el.getAttribute('data-qa-kb')
  const s = getComputedStyle(el)
  const outline = s.outlineStyle !== 'none' && parseFloat(s.outlineWidth) > 0
  const changed = id !== null && kb.styleOf(el) !== kb.items[Number(id)].baseline
  return { id, role: kb.roleOf(el), name: kb.nameOf(el), tag: el.tagName.toLowerCase(), visible_focus: outline || changed }
}
"""

KB_CLEANUP_JS = "() => { document.querySelectorAll('[data-qa-kb-start]').forEach((e) => e.remove()) }"


class FocusStop(BaseModel):
    id: str | None
    role: str
    name: str
    tag: str
    visible_focus: bool

    def describe(self) -> str:
        return f'{self.role} "{self.name}" (<{self.tag}>)'


class KeyboardWalk(BaseModel):
    stops: list[FocusStop]
    trap: FocusStop | None = None
    unreachable: list[FocusStop] = Field(default_factory=list)
    complete: bool = True  # False if the Tab limit was reached before the focus order ended


async def keyboard_walk(page: Page, max_tabs: int = 80) -> KeyboardWalk:
    items = await page.evaluate(KB_PREPARE_JS)
    stops: list[FocusStop] = []
    seen: set[str] = set()
    last_key, repeats, trap = None, 0, None
    complete = False
    try:
        for _ in range(max_tabs):
            await page.keyboard.press("Tab")
            await page.wait_for_timeout(40)
            current = await page.evaluate(KB_CURRENT_JS)
            if current is None or current.get("start"):
                if stops:  # focus left the page or wrapped to the top: the tab order ended
                    complete = True
                    break
                continue
            stop = FocusStop(**current)
            key = stop.id or f"{stop.tag}:{stop.name}"
            if key == last_key:
                repeats += 1
                if repeats >= 2:  # three Tab presses without moving
                    await page.keyboard.press("Shift+Tab")
                    await page.wait_for_timeout(40)
                    trap = stop
                    complete = True
                    break
                continue
            last_key, repeats = key, 0
            if key in seen:  # wrapped around
                complete = True
                break
            seen.add(key)
            stops.append(stop)
    finally:
        await page.evaluate(KB_CLEANUP_JS)

    unreachable: list[FocusStop] = []
    if complete and trap is None:
        reached_groups = {i["group"] for i in items if i["id"] in seen and i["group"]}
        for item in items:
            if item["id"] in seen or (item["group"] and item["group"] in reached_groups):
                continue
            unreachable.append(FocusStop(id=item["id"], role=item["role"], name=item["name"], tag=item["tag"],
                                         visible_focus=True))
    return KeyboardWalk(stops=stops, trap=trap, unreachable=unreachable, complete=complete)


class KeyboardFinding(BaseModel):
    kind: Literal["keyboard-trap", "focus-not-visible", "unreachable-control", "focus-order"]
    title: str
    description: str
    elements: list[str] = Field(default_factory=list)
    how_to_fix: str
    impact: Impact


class KeyboardReview(BaseModel):
    findings: list[KeyboardFinding] = Field(default_factory=list)


def keyboard_candidates(walk: KeyboardWalk) -> list[KeyboardFinding]:
    """Findings straight from the Tab walk, used when the LLM review isn't available."""
    found: list[KeyboardFinding] = []
    if walk.trap:
        found.append(KeyboardFinding(
            kind="keyboard-trap", impact="critical", title=f"Keyboard trap in {walk.trap.describe()}",
            description="Pressing Tab (and Shift+Tab) keeps focus on this element, so keyboard users cannot reach "
                        "the rest of the page.",
            elements=[walk.trap.describe()],
            how_to_fix="Don't call preventDefault() on Tab keydown or move focus back in a blur/keydown handler; "
                       "let Tab and Shift+Tab leave the element."))
    invisible = [s for s in walk.stops if not s.visible_focus]
    if invisible:
        found.append(KeyboardFinding(
            kind="focus-not-visible", impact="serious", title="No visible focus indicator",
            description=f"{len(invisible)} element(s) look exactly the same when focused with the keyboard.",
            elements=[s.describe() for s in invisible],
            how_to_fix="Don't remove the outline without a replacement: add a :focus-visible style, e.g. "
                       "outline: 2px solid #1d4ed8; outline-offset: 2px."))
    if walk.unreachable:
        found.append(KeyboardFinding(
            kind="unreachable-control", impact="serious", title="Interactive elements not reachable with Tab",
            description=f"{len(walk.unreachable)} interactive element(s) never received keyboard focus.",
            elements=[s.describe() for s in walk.unreachable],
            how_to_fix="Use real <button>/<a href> elements, or add tabindex=\"0\" and Enter/Space key handlers "
                       "to custom controls; don't set tabindex=\"-1\" on controls users need."))
    return found


def _keyboard_to_findings(review: list[KeyboardFinding]) -> list[A11yFinding]:
    return [
        A11yFinding(rule_id=f.kind, source="keyboard", impact=f.impact, title=f.title, description=f.description,
                    how_to_fix=f.how_to_fix, wcag=KEYBOARD_WCAG[f.kind],
                    nodes=[IssueNode(target=e) for e in f.elements[:10]])
        for f in review
    ]


async def review_keyboard(llm: LLMProvider, walk: KeyboardWalk, page_url: str) -> list[KeyboardFinding]:
    order = "\n".join(f"{i}. {s.describe()} — focus visible: {'yes' if s.visible_focus else 'no'}"
                      for i, s in enumerate(walk.stops, 1)) or "(Tab never focused anything on the page)"
    prompt = render_prompt(
        "a11y_keyboard",
        page_url=page_url,
        focus_order=order,
        trap=(f"Focus got stuck on {walk.trap.describe()}: Tab and Shift+Tab did not move it." if walk.trap
              else "None found."),
        unreachable="\n".join(f"- {s.describe()}" for s in walk.unreachable) or "None.",
    )
    return (await llm.generate_json(prompt, KeyboardReview)).findings


# ---------- alt-text quality (vision) ----------

IMAGES_JS = """
(maxCount) => {
  const found = []
  for (const img of document.images) {
    const alt = (img.getAttribute('alt') || '').trim()
    const r = img.getBoundingClientRect()
    if (!alt || !img.complete || img.naturalWidth < 24 || r.width < 24 || r.height < 24) continue
    if (getComputedStyle(img).visibility === 'hidden' || img.closest('[aria-hidden="true"]')) continue
    img.setAttribute('data-qa-img', String(found.length))
    found.push({ index: found.length, alt: alt.slice(0, 200), src: img.currentSrc || img.src })
    if (found.length >= maxCount * 3) break
  }
  return found
}
"""


class AltVerdict(BaseModel):
    index: int
    verdict: Literal["good", "poor", "misleading", "decorative"]
    reason: str
    suggested_alt: str = ""


class AltReview(BaseModel):
    results: list[AltVerdict] = Field(default_factory=list)


async def check_alt_text(llm: LLMProvider, page: Page, seen_srcs: set[str]) -> list[A11yFinding]:
    images = [i for i in await page.evaluate(IMAGES_JS, MAX_IMAGES_PER_PAGE) if i["src"] not in seen_srcs]
    images = images[:MAX_IMAGES_PER_PAGE]
    crops: list[bytes] = []
    kept: list[dict] = []
    for img in images:
        try:
            crops.append(await page.locator(f'[data-qa-img="{img["index"]}"]').first.screenshot(timeout=5000))
            kept.append(img)
        except Exception:  # noqa: BLE001 - off-screen or detached image: skip it
            continue
    if not kept:
        return []
    listing = "\n".join(f'Image {n}: alt="{img["alt"]}"' for n, img in enumerate(kept, 1))
    review = await llm.generate_json(render_prompt("a11y_alt_text", page_url=page.url, images=listing), AltReview,
                                     images=crops)
    seen_srcs.update(img["src"] for img in kept)
    bad = [r for r in review.results if r.verdict != "good" and 1 <= r.index <= len(kept)]
    if not bad:
        return []
    nodes = [
        IssueNode(target=f'img alt="{kept[r.index - 1]["alt"]}"', html=kept[r.index - 1]["src"][-120:],
                  summary=f"{r.verdict}: {r.reason}" + (f' Suggested alt: "{r.suggested_alt}"' if r.suggested_alt else ""))
        for r in bad
    ]
    return [A11yFinding(
        rule_id="alt-text-quality", source="vision",
        impact="serious" if any(r.verdict == "misleading" for r in bad) else "moderate",
        title="Images with unhelpful alt text",
        description="The alt text of these images does not describe what they show (checked by a vision model).",
        how_to_fix='Rewrite the alt text to say what the image shows or does (see suggestions); use alt="" for '
                   "purely decorative images.",
        wcag=["1.1.1"], nodes=nodes,
    )]


# ---------- one page ----------


async def audit_page(
    page: Page,
    llm: LLMProvider | None,
    *,
    seen_image_srcs: set[str] | None = None,
    max_tabs: int = 80,
) -> PageAuditResult:
    """Audit the page currently open in `page`. With llm=None the keyboard findings are reported as detected
    and the alt-text check is skipped."""
    seen = seen_image_srcs if seen_image_srcs is not None else set()
    result = PageAuditResult(url=page.url, title=await page.title(), score=100, findings=[])

    try:
        result.findings += await run_axe(page)
    except Exception as exc:  # noqa: BLE001
        result.notes.append(f"axe-core could not run: {str(exc).splitlines()[0]}")

    if llm is not None:
        try:
            result.findings += await check_alt_text(llm, page, seen)
            result.llm_used = True
        except LLMError as exc:  # quota, bad output…: keep going without the vision check
            result.notes.append(f"Alt-text check skipped: {exc}")
            if not isinstance(exc, LLMOutputError):
                result.llm_unavailable, llm = str(exc), None
        except Exception as exc:  # noqa: BLE001
            result.notes.append(f"Alt-text check failed: {str(exc).splitlines()[0]}")

    try:
        walk = await keyboard_walk(page, max_tabs=max_tabs)
        result.focus_stops = len(walk.stops)
        if not walk.complete:
            result.notes.append(f"Keyboard check stopped after {max_tabs} Tab presses.")
        candidates = keyboard_candidates(walk)
        if candidates and llm is not None:
            try:
                result.findings += _keyboard_to_findings(await review_keyboard(llm, walk, page.url))
                result.llm_used = True
            except LLMError as exc:
                result.notes.append(f"Keyboard review by the LLM failed, reporting raw findings: {exc}")
                if not isinstance(exc, LLMOutputError):
                    result.llm_unavailable = str(exc)
                result.findings += _keyboard_to_findings(candidates)
        else:
            result.findings += _keyboard_to_findings(candidates)
    except Exception as exc:  # noqa: BLE001
        result.notes.append(f"Keyboard check failed: {str(exc).splitlines()[0]}")

    result.score = score_findings(result.findings)
    return result
