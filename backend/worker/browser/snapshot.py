"""Compact, numbered accessibility-tree snapshot of the current page.

A script runs in the page, walks the DOM in document order and records every visible element an agent
might care about: interactive controls (with role, accessible name, value, state), headings, images with
alt text, live status/alert messages and other visible text. Each entry gets a number (`ref`) that is also
written to the element as `data-qa-ref`, so tools can act on it with `click(ref)`, `type(ref, …)` etc.
Never raw HTML: the text form is designed to be short enough for an LLM prompt.
"""

from pydantic import BaseModel

REF_ATTRIBUTE = "data-qa-ref"
MAX_ELEMENTS = 400
MAX_TEXT = 160

SNAPSHOT_JS = r"""
({ refAttr, maxElements, maxText }) => {
  document.querySelectorAll(`[${refAttr}]`).forEach((el) => el.removeAttribute(refAttr))

  const SKIP = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE', 'SVG', 'HEAD', 'META', 'LINK', 'IFRAME'])
  const INTERACTIVE = new Set(['link', 'button', 'textbox', 'searchbox', 'combobox', 'listbox', 'checkbox',
    'radio', 'slider', 'spinbutton', 'switch', 'tab', 'menuitem', 'menuitemcheckbox', 'menuitemradio', 'option'])
  const LIVE = new Set(['status', 'alert', 'dialog', 'alertdialog'])

  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim()
  const clip = (s) => (s.length > maxText ? s.slice(0, maxText - 1) + '…' : s)

  const isVisible = (el) => {
    const style = getComputedStyle(el)
    if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) return false
    const rect = el.getBoundingClientRect()
    return rect.width > 0 && rect.height > 0
  }

  const implicitRole = (el) => {
    const tag = el.tagName
    const type = (el.getAttribute('type') || 'text').toLowerCase()
    if (tag === 'A' || tag === 'AREA') return el.hasAttribute('href') ? 'link' : null
    if (tag === 'BUTTON' || tag === 'SUMMARY') return 'button'
    if (tag === 'SELECT') return el.multiple || el.size > 1 ? 'listbox' : 'combobox'
    if (tag === 'TEXTAREA') return 'textbox'
    if (tag === 'INPUT') {
      if (type === 'hidden') return null
      if (['button', 'submit', 'reset', 'image'].includes(type)) return 'button'
      if (type === 'checkbox') return 'checkbox'
      if (type === 'radio') return 'radio'
      if (type === 'range') return 'slider'
      if (type === 'number') return 'spinbutton'
      if (type === 'search') return 'searchbox'
      return 'textbox'
    }
    if (/^H[1-6]$/.test(tag)) return 'heading'
    if (tag === 'IMG') return 'img'
    if (tag === 'DIALOG') return 'dialog'
    return null
  }

  const textWithoutControls = (el) => {
    const copy = el.cloneNode(true)
    copy.querySelectorAll('input, select, textarea, button').forEach((c) => c.remove())
    return clean(copy.textContent)
  }

  const accessibleName = (el, role) => {
    const ariaLabel = clean(el.getAttribute('aria-label'))
    if (ariaLabel) return ariaLabel
    const labelledBy = el.getAttribute('aria-labelledby')
    if (labelledBy) {
      const text = labelledBy.split(/\s+/).map((id) => document.getElementById(id))
        .filter(Boolean).map((e) => clean(e.textContent)).join(' ')
      if (text) return text
    }
    if (el.labels && el.labels.length) {
      const text = [...el.labels].map(textWithoutControls).join(' ')
      if (text) return text
    }
    if (el.tagName === 'IMG' || (el.tagName === 'INPUT' && el.type === 'image')) return clean(el.getAttribute('alt'))
    if (el.tagName === 'INPUT' && ['button', 'submit', 'reset'].includes(el.type)) {
      return clean(el.value) || (el.type === 'submit' ? 'Submit' : el.type === 'reset' ? 'Reset' : '')
    }
    if (['link', 'button', 'heading', 'tab', 'menuitem', 'option', 'switch', 'checkbox', 'radio'].includes(role)) {
      const text = clean(el.innerText)
      if (text) return text
      const img = el.querySelector('img[alt]')
      if (img) return clean(img.getAttribute('alt'))
    }
    return clean(el.getAttribute('title')) || clean(el.getAttribute('placeholder'))
  }

  const entries = []
  const captured = new Set()
  const hasCapturedAncestor = (el) => {
    for (let p = el.parentElement; p; p = p.parentElement) if (captured.has(p)) return true
    return false
  }
  let truncated = false

  const add = (el, entry, { claimsDescendants = true } = {}) => {
    if (entries.length >= maxElements) { truncated = true; return }
    entry.ref = entries.length + 1
    el.setAttribute(refAttr, String(entry.ref))
    if (claimsDescendants) captured.add(el)
    entries.push(entry)
  }
  const HAS_OWN_ENTRY = 'a[href], button, input, select, textarea, [role], h1, h2, h3, h4, h5, h6, img[alt]'

  const walker = document.createTreeWalker(document.body || document.documentElement, NodeFilter.SHOW_ELEMENT, {
    acceptNode: (node) => (SKIP.has(node.tagName.toUpperCase()) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT),
  })

  for (let el = walker.currentNode; el; el = walker.nextNode()) {
    if (el === document.body) continue
    if (el.closest('[aria-hidden="true"]')) continue
    const role = (el.getAttribute('role') || '').split(/\s+/)[0] || implicitRole(el)

    if (role === 'option' && el.closest('select')) continue  // listed on the select itself
    if (role && (INTERACTIVE.has(role) || role === 'heading' || role === 'img' || LIVE.has(role))) {
      if (!isVisible(el)) continue
      // A heading or image inside a link/button is already part of that control's name.
      if ((role === 'heading' || role === 'img') && hasCapturedAncestor(el)) continue
      if (role === 'img' && !clean(el.getAttribute('alt')) && !el.getAttribute('aria-label')) continue
      const entry = { role, name: clip(accessibleName(el, role)) }
      if (role === 'heading') entry.level = Number(el.getAttribute('aria-level') || el.tagName.slice(1)) || null
      if (LIVE.has(role)) {
        const text = clip(clean(el.innerText))
        if (!text && !entry.name) continue
        entry.text = text
      }
      if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') {
        if (el.type === 'checkbox' || el.type === 'radio') entry.checked = el.checked
        else if (el.value) entry.value = el.type === 'password' ? '••••••' : clip(el.value)
        if (el.required) entry.required = true
      } else if (el.tagName === 'SELECT') {
        const selected = el.selectedOptions[0]
        if (selected) entry.value = clip(clean(selected.textContent))
        entry.options = [...el.options].slice(0, 25).map((o) => clip(clean(o.textContent)))
      } else if (el.getAttribute('aria-checked')) {
        entry.checked = el.getAttribute('aria-checked') === 'true'
      }
      if (role === 'link' && el.href) {
        const target = new URL(el.href, location.href)
        entry.href = target.origin === location.origin ? target.pathname + target.search : target.href
      }
      const form = el.form || el.closest('form')
      if (form) entry.form = [...document.forms].indexOf(form)
      if (el.disabled || el.getAttribute('aria-disabled') === 'true') entry.disabled = true
      if (el.getAttribute('aria-expanded')) entry.expanded = el.getAttribute('aria-expanded') === 'true'
      add(el, entry)
      continue
    }

    // Plain visible text that belongs to this element directly (not to a captured control/heading).
    if (el.tagName === 'LABEL' && el.control) continue  // already the control's name
    const own = clean([...el.childNodes].filter((n) => n.nodeType === Node.TEXT_NODE).map((n) => n.textContent).join(' '))
    if (!own || hasCapturedAncestor(el) || !isVisible(el)) continue
    if (el.querySelector(HAS_OWN_ENTRY)) {
      // Mixed content like "New here? <a>Create an account</a>": keep just this element's own words;
      // the link etc. get their own entries.
      add(el, { role: 'text', name: '', text: clip(own) }, { claimsDescendants: false })
    } else {
      // Plain formatted text like "$39.99 <s>$49.99</s>": one entry with all of it.
      add(el, { role: 'text', name: '', text: clip(clean(el.innerText)) })
    }
  }

  return { url: location.href, title: document.title, elements: entries, truncated }
}
"""


class SnapshotElement(BaseModel):
    ref: int
    role: str
    name: str = ""
    text: str | None = None
    value: str | None = None
    level: int | None = None
    checked: bool | None = None
    disabled: bool | None = None
    required: bool | None = None
    expanded: bool | None = None
    options: list[str] | None = None
    href: str | None = None  # links: same-origin path, or the full URL for other sites
    form: int | None = None  # index of the enclosing <form> (document order), for controls inside one

    def to_line(self) -> str:
        if self.role == "text":
            return f"[{self.ref}] text: {self.text}"
        parts = [f"[{self.ref}] {self.role}", f'"{self.name}"']
        if self.level:
            parts.append(f"level={self.level}")
        if self.value is not None:
            parts.append(f'value="{self.value}"')
        if self.checked is not None:
            parts.append("checked" if self.checked else "unchecked")
        if self.expanded is not None:
            parts.append("expanded" if self.expanded else "collapsed")
        for flag in ("disabled", "required"):
            if getattr(self, flag):
                parts.append(flag)
        if self.options:
            parts.append("options=[" + ", ".join(self.options) + "]")
        if self.text and self.text != self.name:
            parts.append(f"text: {self.text}")
        return " ".join(parts)


class Snapshot(BaseModel):
    url: str
    title: str
    elements: list[SnapshotElement]
    truncated: bool = False

    def to_text(self) -> str:
        lines = [f"URL: {self.url}", f"Title: {self.title or '(none)'}"]
        lines += [e.to_line() for e in self.elements]
        if self.truncated:
            lines.append(f"… (truncated at {len(self.elements)} elements)")
        return "\n".join(lines)

    def find(self, ref: int) -> SnapshotElement | None:
        return next((e for e in self.elements if e.ref == ref), None)
