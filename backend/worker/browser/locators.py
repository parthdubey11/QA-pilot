"""Recording and resolving element locators for saved tests.

When the executor acts on an element we record several independent locators (role+name, visible text, CSS,
position). On replay we try them in that order. A match found by a *fallback* locator is only accepted when it
is the same kind of element with a similar name, so a renamed button can be found at its old place, but an
unrelated element that happens to be there is not.
"""

from difflib import SequenceMatcher

from playwright.async_api import Locator, Page

from app.models.saved_test import Locators
from worker.browser.snapshot import REF_ATTRIBUTE, SnapshotElement

ARIA_ROLES = {"link", "button", "textbox", "searchbox", "combobox", "listbox", "checkbox", "radio", "slider",
              "spinbutton", "switch", "tab", "menuitem", "option", "heading", "img"}
MIN_SIMILARITY = 0.6

DESCRIBE_JS = """
(el) => {
  const clean = (t) => (t || '').replace(/\\s+/g, ' ').trim()
  const unique = (sel) => { try { return document.querySelectorAll(sel).length === 1 } catch { return false } }
  let css = null
  if (el.id && unique('#' + CSS.escape(el.id))) css = '#' + CSS.escape(el.id)
  const tag = el.tagName.toLowerCase()
  if (!css && el.getAttribute('name')) {
    const sel = `${tag}[name="${CSS.escape(el.getAttribute('name'))}"]`
    if (unique(sel)) css = sel
  }
  if (!css) {
    const parts = []
    for (let node = el; node && node.nodeType === 1 && parts.length < 5; node = node.parentElement) {
      if (node.id && unique('#' + CSS.escape(node.id))) { parts.unshift('#' + CSS.escape(node.id)); break }
      const siblings = node.parentElement ? [...node.parentElement.children].filter((c) => c.tagName === node.tagName) : []
      const t = node.tagName.toLowerCase()
      parts.unshift(siblings.length > 1 ? `${t}:nth-of-type(${siblings.indexOf(node) + 1})` : t)
    }
    const sel = parts.join(' > ')
    if (unique(sel)) css = sel
  }
  const r = el.getBoundingClientRect()
  const field = ['input', 'textarea', 'select'].includes(tag)  // never record a field's value (could be a password)
  return { tag, css, text: field ? null : clean(el.innerText).slice(0, 80) || null,
           x: Math.round(r.left + r.width / 2 + window.scrollX), y: Math.round(r.top + r.height / 2 + window.scrollY) }
}
"""

POINT_JS = """
([x, y]) => {
  window.scrollTo(0, Math.max(0, y - window.innerHeight / 2))
  const el = document.elementFromPoint(x - window.scrollX, y - window.scrollY)
  if (!el) return null
  const target = el.closest('a, button, input, select, textarea, [role], [onclick], label') || el
  const marker = 'qa-point-' + Math.random().toString(36).slice(2)
  target.setAttribute('data-qa-point', marker)
  return marker
}
"""


def similarity(a: str | None, b: str | None) -> float:
    if not a or not b:
        return 0.0
    a, b = a.lower().strip(), b.lower().strip()
    return 1.0 if a in b or b in a else SequenceMatcher(None, a, b).ratio()


async def describe_element(page: Page, ref: int, element: SnapshotElement | None) -> Locators:
    """Locators for the element with snapshot ref `ref` (call before acting on it)."""
    handle = page.locator(f'[{REF_ATTRIBUTE}="{int(ref)}"]').first
    info = await handle.evaluate(DESCRIBE_JS)
    role = element.role if element and element.role in ARIA_ROLES else None
    return Locators(role=role, name=(element.name or None) if role else None, text=info["text"], css=info["css"],
                    tag=info["tag"], x=info["x"], y=info["y"])


async def _single_visible(locator: Locator) -> Locator | None:
    try:
        count = await locator.count()
    except Exception:  # noqa: BLE001 - invalid selector etc.
        return None
    visible = [locator.nth(i) for i in range(min(count, 5)) if await locator.nth(i).is_visible()]
    return visible[0] if len(visible) == 1 else None


async def _facts(candidate: Locator) -> dict:
    return await candidate.evaluate(
        """(el) => ({ tag: el.tagName.toLowerCase(),
                     text: (el.getAttribute('aria-label') || el.innerText || el.getAttribute('placeholder') || '')
                       .replace(/\\s+/g, ' ').trim().slice(0, 80) })"""
    )


async def resolve(page: Page, loc: Locators) -> tuple[Locator | None, str | None]:
    """Find the recorded element. Returns (locator, method). method is None when the element was found by the
    first locator the step has (usually role+name); otherwise it names the fallback that found it after an
    earlier locator failed ("text", "css" or "position") — i.e. the step was healed."""
    wanted_name = loc.name or loc.text
    earlier_failed = False

    def verdict(found: Locator, method: str) -> tuple[Locator, str | None]:
        return found, (method if earlier_failed else None)

    if loc.role and loc.name:
        found = await _single_visible(page.get_by_role(loc.role, name=loc.name, exact=True))  # type: ignore[arg-type]
        if found:
            return verdict(found, "role")
        earlier_failed = True
    if loc.text:
        found = await _single_visible(page.get_by_text(loc.text, exact=True))
        if found and (await _facts(found))["tag"] == loc.tag:
            return verdict(found, "text")
        earlier_failed = True
    if loc.css:
        found = await _single_visible(page.locator(loc.css))
        if found and (await _facts(found))["tag"] == loc.tag:
            facts = await _facts(found)
            # an input keeps its CSS (name=…) when its label changes; for buttons/links require a similar name
            if loc.tag in ("input", "select", "textarea") or not earlier_failed                     or similarity(facts["text"], wanted_name) >= MIN_SIMILARITY:
                return verdict(found, "css")
        earlier_failed = True
    if loc.x is not None and loc.y is not None:
        marker = await page.evaluate(POINT_JS, [loc.x, loc.y])
        if marker:
            found = page.locator(f'[data-qa-point="{marker}"]')
            facts = await _facts(found)
            if facts["tag"] == loc.tag and (
                loc.tag in ("input", "select", "textarea") or similarity(facts["text"], wanted_name) >= MIN_SIMILARITY
            ):
                return found, "position"  # position is never trusted as a first choice
    return None, None


async def locators_for(page: Page, found: Locator, old: Locators) -> Locators:
    """Fresh locators for an element found by a fallback/healer (keeps the old role if the element has none)."""
    info = await found.evaluate(DESCRIBE_JS)
    name = await found.evaluate(
        """(el) => (el.getAttribute('aria-label') || (el.labels && el.labels[0] && el.labels[0].innerText) ||
                    el.innerText || el.getAttribute('placeholder') || '').replace(/\\s+/g, ' ').trim().slice(0, 80)"""
    )
    return Locators(role=old.role, name=name or None, text=info["text"], css=info["css"], tag=info["tag"],
                    x=info["x"], y=info["y"])
