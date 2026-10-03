"""Explorer: crawls same-origin pages and builds a site map (pages, forms, buttons, links).

No LLM and no side effects: it only follows links (GET navigation). It never submits forms or clicks buttons,
and skips links that look destructive (log out, delete…). Limits: max depth and max pages.
"""

import re
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from worker.browser import BrowserSession, Snapshot, ToolError

SKIP_LINK_TEXT = re.compile(r"\b(log ?out|sign ?out|delete|remove|unsubscribe|cancel account)\b", re.IGNORECASE)
SKIP_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".pdf", ".zip", ".css", ".js", ".json", ".xml")
FIELD_ROLES = {"textbox", "searchbox", "combobox", "listbox", "checkbox", "radio", "spinbutton", "slider", "switch"}


class FormField(BaseModel):
    role: str
    label: str
    required: bool = False
    options: list[str] | None = None

    def describe(self) -> str:
        text = f'{self.role} "{self.label}"' + (" required" if self.required else "")
        return text + (f" options=[{', '.join(self.options[:8])}]" if self.options else "")


class PageForm(BaseModel):
    fields: list[FormField] = Field(default_factory=list)
    buttons: list[str] = Field(default_factory=list)


class PageLink(BaseModel):
    text: str
    path: str


class SitePage(BaseModel):
    path: str
    title: str
    depth: int
    headings: list[str] = Field(default_factory=list)
    texts: list[str] = Field(default_factory=list)  # short visible texts (prices, stock, messages…)
    forms: list[PageForm] = Field(default_factory=list)
    controls: list[FormField] = Field(default_factory=list)  # fields outside any <form>
    buttons: list[str] = Field(default_factory=list)  # buttons outside any <form>
    links: list[PageLink] = Field(default_factory=list)
    error: str | None = None


class SiteMap(BaseModel):
    base_url: str
    pages: list[SitePage]

    def to_text(self, max_chars: int = 12_000) -> str:
        """Compact description for the planner. Links already listed on an earlier page aren't repeated."""
        lines = [f"Site map of {self.base_url} ({len(self.pages)} pages explored)"]
        seen_links: set[str] = set()
        for page in self.pages:
            lines.append(f'\nPage {page.path} — "{page.title or "(no title)"}"')
            if page.error:
                lines.append(f"  could not open: {page.error}")
                continue
            if page.headings:
                lines.append("  headings: " + " | ".join(page.headings[:8]))
            if page.texts:
                lines.append("  text: " + " | ".join(page.texts[:10]))
            for i, form in enumerate(page.forms):
                fields = ", ".join(f.describe() for f in form.fields) or "(no fields)"
                lines.append(f"  form {i + 1}: {fields}; buttons: {', '.join(form.buttons) or '(none)'}")
            if page.controls:
                lines.append("  controls: " + ", ".join(c.describe() for c in page.controls))
            if page.buttons:
                lines.append("  buttons: " + ", ".join(page.buttons[:15]))
            new_links = [link for link in page.links if (link.text, link.path) not in seen_links]
            seen_links.update((link.text, link.path) for link in page.links)
            if new_links:
                lines.append("  links: " + ", ".join(f'"{link.text or "(no text)"}" → {link.path}' for link in new_links[:25]))
        text = "\n".join(lines)
        return text if len(text) <= max_chars else text[: max_chars - 20] + "\n… (truncated)"


def normalize_path(href: str) -> str | None:
    """Same-origin path to crawl, or None. Drops fragments; keeps the query string."""
    if not href.startswith("/") or href.startswith("//"):
        return None
    parts = urlsplit(href)
    path = parts.path or "/"
    if path.lower().endswith(SKIP_EXTENSIONS):
        return None
    return path + (f"?{parts.query}" if parts.query else "")


def path_of(url: str) -> str | None:
    parts = urlsplit(url)
    return normalize_path((parts.path or "/") + (f"?{parts.query}" if parts.query else ""))


def page_from_snapshot(snapshot: Snapshot, path: str, depth: int) -> SitePage:
    page = SitePage(path=path, title=snapshot.title, depth=depth)
    forms: dict[int, PageForm] = {}
    for el in snapshot.elements:
        if el.role == "heading" and el.name:
            page.headings.append(el.name)
        elif el.role in {"text", "status", "alert"} and el.text and len(el.text) <= 120:
            page.texts.append(el.text)
        elif el.role in FIELD_ROLES:
            field = FormField(role=el.role, label=el.name, required=bool(el.required), options=el.options)
            if el.form is not None:
                forms.setdefault(el.form, PageForm()).fields.append(field)
            else:
                page.controls.append(field)
        elif el.role == "button" and not el.disabled:
            label = el.name or "(unnamed button)"
            if el.form is not None:
                forms.setdefault(el.form, PageForm()).buttons.append(label)
            else:
                page.buttons.append(label)
        elif el.role == "link" and el.href:
            link_path = normalize_path(el.href)
            if link_path and PageLink(text=el.name, path=link_path) not in page.links:
                page.links.append(PageLink(text=el.name, path=link_path))
    page.forms = [forms[i] for i in sorted(forms)]
    return page


PageCallback = Callable[[SitePage, Snapshot | None], Awaitable[None]]


async def explore(
    session: BrowserSession,
    base_url: str,
    *,
    max_pages: int = 12,
    max_depth: int = 2,
    on_page: PageCallback | None = None,
) -> SiteMap:
    start = path_of(base_url) or "/"
    queue: list[tuple[str, int]] = [(start, 0)]
    queued = {start}
    visited_final: set[str] = set()
    pages: list[SitePage] = []

    while queue and len(pages) < max_pages:
        path, depth = queue.pop(0)
        try:
            await session.goto(path)
        except ToolError as exc:
            page = SitePage(path=path, title="", depth=depth, error=str(exc))
            pages.append(page)
            if on_page:
                await on_page(page, None)
            continue
        # A redirect (e.g. /checkout → /login) lands on a page we may already have.
        final = path_of(session.url) or path
        if final in visited_final:
            continue
        visited_final.add(final)
        snapshot = await session.snapshot()
        page = page_from_snapshot(snapshot, final, depth)
        pages.append(page)
        if on_page:
            await on_page(page, snapshot)
        if depth >= max_depth:
            continue
        for link in page.links:
            if link.path not in queued and not SKIP_LINK_TEXT.search(link.text):
                queued.add(link.path)
                queue.append((link.path, depth + 1))

    return SiteMap(base_url=base_url, pages=pages)
