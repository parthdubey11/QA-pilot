"""Playwright session wrapper exposing the agents' typed tool set.

Safety: navigation is limited to the project's own origin, and every action is followed by a short pause
(ACTION_DELAY_MS) so we don't hammer the site under test.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from playwright.async_api import Browser, Locator, Page, Route, async_playwright
from playwright.async_api import Error as PlaywrightError

from app.core.targets import blocked_reason, browser_args
from worker.browser.secrets import SecretVault
from worker.browser.snapshot import MAX_ELEMENTS, MAX_TEXT, REF_ATTRIBUTE, SNAPSHOT_JS, Snapshot
from worker.browser.tools import (
    Back,
    Click,
    Done,
    Goto,
    Press,
    Scroll,
    Select,
    TakeScreenshot,
    TakeSnapshot,
    ToolCall,
    ToolError,
    ToolResult,
    Type,
    Wait,
)

DESKTOP_VIEWPORT = {"width": 1280, "height": 800}
MOBILE_VIEWPORT = {"width": 390, "height": 844}


def origin_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}".lower()


class BrowserLauncher:
    """One Chromium for a whole run; every test case gets a fresh context (own cookies, storage, cart)."""

    def __init__(self, browser: Browser):
        self.browser = browser

    @classmethod
    @asynccontextmanager
    async def start(cls, *, headless: bool = True, channel: str | None = None) -> AsyncIterator["BrowserLauncher"]:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=headless, channel=channel or None, args=browser_args())
            try:
                yield cls(browser)
            finally:
                await browser.close()

    @asynccontextmanager
    async def session(
        self,
        base_url: str,
        *,
        mobile: bool = False,
        action_delay_ms: int = 300,
        timeout_ms: int = 20000,
        vault: SecretVault | None = None,
    ) -> AsyncIterator["BrowserSession"]:
        context = await self.browser.new_context(
            viewport=MOBILE_VIEWPORT if mobile else DESKTOP_VIEWPORT, is_mobile=mobile, has_touch=mobile
        )
        try:
            # SSRF guard: no request (navigation, redirect or the site's own fetch/XHR) may reach blocked hosts.
            await context.route(lambda url: blocked_reason(url) is not None, abort_blocked)
            page = await context.new_page()
            yield BrowserSession(page, allowed_origin=origin_of(base_url), action_delay_ms=action_delay_ms,
                                 timeout_ms=timeout_ms, vault=vault)
        finally:
            await context.close()


async def abort_blocked(route: Route) -> None:
    await route.abort("blockedbyclient")


class BrowserSession:
    def __init__(
        self,
        page: Page,
        *,
        allowed_origin: str,
        action_delay_ms: int = 300,
        timeout_ms: int = 20000,
        vault: SecretVault | None = None,
    ):
        self.page = page
        self.allowed_origin = allowed_origin.lower()
        self.action_delay_ms = action_delay_ms
        self.timeout_ms = timeout_ms
        self.vault = vault or SecretVault()
        page.set_default_timeout(timeout_ms)

    # ---------- lifecycle ----------

    @classmethod
    @asynccontextmanager
    async def launch(
        cls,
        base_url: str,
        *,
        headless: bool = True,
        channel: str | None = None,
        mobile: bool = False,
        action_delay_ms: int = 300,
        timeout_ms: int = 20000,
        vault: SecretVault | None = None,
    ) -> AsyncIterator["BrowserSession"]:
        """A single session with its own browser (for one-off use; runs use BrowserLauncher)."""
        async with BrowserLauncher.start(headless=headless, channel=channel) as launcher:
            async with launcher.session(base_url, mobile=mobile, action_delay_ms=action_delay_ms,
                                        timeout_ms=timeout_ms, vault=vault) as session:
                yield session

    @property
    def url(self) -> str:
        return self.page.url

    # ---------- tools ----------

    async def goto(self, url: str) -> ToolResult:
        target = urljoin(self.page.url if self.page.url.startswith("http") else self.allowed_origin + "/", url)
        if origin_of(target) != self.allowed_origin:
            raise ToolError(f"Navigation to {target} is outside the project ({self.allowed_origin})")
        try:
            response = await self.page.goto(target, wait_until="domcontentloaded")
        except PlaywrightError as exc:
            raise ToolError(f"Could not open {target}: {first_line(exc)}") from exc
        await self._settle()
        status = f" (HTTP {response.status})" if response is not None else ""
        return ToolResult(ok=True, message=f"Opened {self.page.url}{status}", url=self.page.url)

    async def click(self, ref: int) -> ToolResult:
        el = await self._element(ref)
        await self._act(el.click(), f"click [{ref}]")
        return ToolResult(ok=True, message=f"Clicked [{ref}]", url=self.page.url)

    async def type(self, ref: int, text: str) -> ToolResult:
        el = await self._element(ref)
        try:
            real_text = self.vault.fill(text)  # {{cred:…}} placeholders -> real values, only here
        except KeyError as exc:
            raise ToolError(str(exc.args[0])) from None
        await self._act(el.fill(real_text), f"type into [{ref}]")
        return ToolResult(ok=True, message=f"Typed into [{ref}]", url=self.page.url)

    async def select(self, ref: int, value: str) -> ToolResult:
        el = await self._element(ref)
        try:
            await el.select_option(label=value, timeout=self.timeout_ms)
        except PlaywrightError:
            await self._act(el.select_option(value=value), f"select {value!r} in [{ref}]")
        await self._settle()
        return ToolResult(ok=True, message=f"Selected {value!r} in [{ref}]", url=self.page.url)

    async def press(self, key: str) -> ToolResult:
        await self._act(self.page.keyboard.press(key), f"press {key}")
        return ToolResult(ok=True, message=f"Pressed {key}", url=self.page.url)

    async def scroll(self, direction: str = "down") -> ToolResult:
        delta = 600 if direction == "down" else -600
        await self._act(self.page.mouse.wheel(0, delta), f"scroll {direction}")
        return ToolResult(ok=True, message=f"Scrolled {direction}", url=self.page.url)

    async def back(self) -> ToolResult:
        await self._act(self.page.go_back(wait_until="domcontentloaded"), "go back")
        return ToolResult(ok=True, message=f"Went back to {self.page.url}", url=self.page.url)

    async def wait(self, ms: int) -> ToolResult:
        await asyncio.sleep(min(max(ms, 0), 10_000) / 1000)
        return ToolResult(ok=True, message=f"Waited {ms} ms", url=self.page.url)

    async def snapshot(self) -> Snapshot:
        raw = await self.page.evaluate(
            SNAPSHOT_JS, {"refAttr": REF_ATTRIBUTE, "maxElements": MAX_ELEMENTS, "maxText": MAX_TEXT}
        )
        return Snapshot.model_validate(raw)

    def redact(self, text: str) -> str:
        """Hide credential values before text goes to the LLM or into saved steps."""
        return self.vault.redact(text)

    async def screenshot(self, path: Path | None = None, *, full_page: bool = False) -> bytes:
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
        return await self.page.screenshot(path=path, full_page=full_page, type="png")

    async def execute(self, call: ToolCall) -> ToolResult:
        """Run one validated tool call (as chosen by an agent)."""
        match call:
            case Goto(url=url):
                return await self.goto(url)
            case Click(ref=ref):
                return await self.click(ref)
            case Type(ref=ref, text=text):
                return await self.type(ref, text)
            case Select(ref=ref, value=value):
                return await self.select(ref, value)
            case Press(key=key):
                return await self.press(key)
            case Scroll(direction=direction):
                return await self.scroll(direction)
            case Back():
                return await self.back()
            case Wait(ms=ms):
                return await self.wait(ms)
            case TakeSnapshot():
                snap = await self.snapshot()
                return ToolResult(ok=True, message=self.redact(snap.to_text()), url=snap.url)
            case TakeScreenshot():
                await self.screenshot()
                return ToolResult(ok=True, message="Screenshot taken", url=self.page.url)
            case Done(result=result, reason=reason):
                return ToolResult(ok=True, message=f"Done: {result} — {reason}", url=self.page.url)
        raise ToolError(f"Unknown tool call: {call!r}")

    # ---------- helpers ----------

    async def _element(self, ref: int) -> Locator:
        locator = self.page.locator(f'[{REF_ATTRIBUTE}="{int(ref)}"]')
        if await locator.count() == 0:
            raise ToolError(f"No element [{ref}] on the page; take a new snapshot")
        return locator.first

    async def _act(self, action, description: str) -> None:  # noqa: ANN001 - a Playwright awaitable
        try:
            await action
        except PlaywrightError as exc:
            raise ToolError(f"Could not {description}: {first_line(exc)}") from exc
        await self._settle()

    async def settle(self) -> None:
        """Wait for navigation/XHR after an action done outside the tool methods (e.g. by the replayer)."""
        await self._settle()

    async def _settle(self) -> None:
        """Let navigation/XHR triggered by the last action finish, then pause briefly."""
        try:
            await self.page.wait_for_load_state("domcontentloaded", timeout=self.timeout_ms)
            await self.page.wait_for_load_state("networkidle", timeout=3000)
        except PlaywrightError:
            pass  # a page that keeps polling never goes idle; that's fine
        if origin_of(self.page.url) != self.allowed_origin and self.page.url.startswith("http"):
            outside = self.page.url
            await self.page.go_back(wait_until="domcontentloaded")
            raise ToolError(f"The action left the project site (went to {outside}); navigated back")
        await asyncio.sleep(self.action_delay_ms / 1000)


def first_line(exc: Exception) -> str:
    return str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
