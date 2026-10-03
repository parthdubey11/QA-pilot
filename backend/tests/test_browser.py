"""BrowserSession + snapshot against a real Chromium. The "site" is served by page.route, so no server is needed.

Skipped if no browser is available. Locally, set BROWSER_CHANNEL=msedge (or chrome) to use an installed browser;
the worker container has Playwright's Chromium.
"""

import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from playwright.async_api import Browser, Route, async_playwright
from pydantic import TypeAdapter

from worker.browser import BrowserSession, ToolCall, ToolError

pytestmark = pytest.mark.asyncio(loop_scope="module")

SITE = "http://shop.test"
PAGES = {
    "/": """<!doctype html><html lang="en"><head><title>Test Shop</title></head><body>
      <nav><a href="/cart">Cart</a> <a href="https://evil.example/">Elsewhere</a>
        <a href="/p/1"><img src="p.png" alt="Shoe photo"><h2>Trail shoes card</h2></a>
        <a href="/cart"><svg aria-hidden="true"></svg></a></nav>
      <main>
        <h1>Products</h1>
        <p>Free shipping on orders over $50.</p>
        <p class="price">$39.99 <s>$49.99</s> <em>sale</em></p>
        <p>New here? <a href="/signup">Create an account</a></p>
        <img src="x.png" alt="Trail shoes"> <img src="decor.png" alt="">
        <form onsubmit="event.preventDefault(); document.getElementById('status').textContent = 'Hello ' + this.name.value">
          <label for="name">Your name</label> <input id="name" name="name" required>
          <label>Password <input type="password" name="pw" value="hunter2"></label>
          <input aria-label="Search products" type="search" placeholder="Search…">
          <label><input type="checkbox" id="terms" checked> I agree</label>
          <label for="country">Country</label>
          <select id="country"><option value="">Choose</option><option value="in">India</option><option value="uk">United Kingdom</option></select>
          <button type="submit">Say hello</button>
          <button type="button" disabled>Disabled action</button>
          <button type="button" aria-label="Close"><svg aria-hidden="true"></svg></button>
        </form>
        <p role="status" id="status"></p>
        <div style="display:none"><button>Hidden button</button><p>Hidden text</p></div>
        <div aria-hidden="true"><p>Decorative text</p></div>
      </main></body></html>""",
    "/cart": """<!doctype html><html><head><title>Cart</title></head><body><h1>Your cart</h1>
      <p>Your cart is empty.</p><a href="/">Back to shop</a></body></html>""",
}


async def serve_site(route: Route) -> None:
    path = "/" + route.request.url.removeprefix(SITE).lstrip("/").split("?")[0]
    if path in PAGES:
        await route.fulfill(status=200, content_type="text/html", body=PAGES[path])
    elif path.endswith(".png"):
        await route.fulfill(status=200, content_type="image/png", body=b"")
    else:
        await route.fulfill(status=404, body="not found")


@pytest_asyncio.fixture(loop_scope="module", scope="module")
async def browser() -> AsyncIterator[Browser]:
    async with async_playwright() as pw:
        try:
            b = await pw.chromium.launch(channel=os.getenv("BROWSER_CHANNEL") or None)
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"No browser available ({str(exc).splitlines()[0]}); set BROWSER_CHANNEL=msedge locally")
        yield b
        await b.close()


@pytest_asyncio.fixture(loop_scope="module")
async def session(browser: Browser) -> AsyncIterator[BrowserSession]:
    context = await browser.new_context()
    await context.route("**/*", serve_site)
    page = await context.new_page()
    s = BrowserSession(page, allowed_origin=SITE, action_delay_ms=0, timeout_ms=5000)
    await s.goto(SITE + "/")
    yield s
    await context.close()


async def test_snapshot_lists_elements_with_role_name_value(session: BrowserSession) -> None:
    snap = await session.snapshot()
    text = snap.to_text()
    lines = text.splitlines()

    assert lines[0] == "URL: http://shop.test/" and lines[1] == "Title: Test Shop"
    assert '[1] link "Cart"' in text
    assert 'heading "Products" level=1' in text
    assert "text: Free shipping on orders over $50." in text
    assert "text: $39.99 $49.99 sale" in text  # inline formatting kept together
    assert "text: New here?\n" in text and 'link "Create an account"' in text  # mixed content split
    assert 'img "Trail shoes"' in text
    assert 'textbox "Your name" required' in text
    assert 'textbox "Password" value="••••••"' in text and "hunter2" not in text  # never leak passwords
    assert 'searchbox "Search products"' in text
    assert 'checkbox "I agree" checked' in text
    assert 'combobox "Country" value="Choose" options=[Choose, India, United Kingdom]' in text
    assert 'button "Say hello"' in text
    assert 'button "Disabled action" disabled' in text
    assert 'button "Close"' in text
    assert 'link ""' in text  # icon-only link with no name is visible to the agent
    assert 'link "Trail shoes card"' in text  # heading/img inside a link are folded into the link
    assert 'heading "Trail shoes card"' not in text and 'img "Shoe photo"' not in text
    for hidden in ("Hidden button", "Hidden text", "Decorative text"):
        assert hidden not in text
    assert [e.ref for e in snap.elements] == list(range(1, len(snap.elements) + 1))


async def test_refs_drive_type_select_click_and_status_text(session: BrowserSession) -> None:
    snap = await session.snapshot()
    ref = {(e.role, e.name): e.ref for e in snap.elements}

    await session.type(ref[("textbox", "Your name")], "Parth")
    await session.select(ref[("combobox", "Country")], "India")
    await session.click(ref[("button", "Say hello")])

    after = await session.snapshot()
    text = after.to_text()
    assert 'textbox "Your name" value="Parth"' in text
    assert 'combobox "Country" value="India"' in text
    assert 'status "" text: Hello Parth' in text


async def test_select_accepts_option_value_too(session: BrowserSession) -> None:
    snap = await session.snapshot()
    country = next(e.ref for e in snap.elements if e.name == "Country")

    await session.select(country, "uk")

    assert 'value="United Kingdom"' in (await session.snapshot()).to_text()


async def test_press_scroll_wait_back(session: BrowserSession) -> None:
    snap = await session.snapshot()
    await session.click(next(e.ref for e in snap.elements if e.role == "link" and e.name == "Cart"))
    assert session.url == SITE + "/cart"
    assert "text: Your cart is empty." in (await session.snapshot()).to_text()

    assert (await session.back()).url == SITE + "/"
    await session.press("Tab")
    await session.scroll("down")
    assert (await session.wait(10)).ok


async def test_unknown_or_stale_ref_is_a_tool_error(session: BrowserSession) -> None:
    await session.snapshot()
    with pytest.raises(ToolError, match=r"No element \[999\]"):
        await session.click(999)


async def test_navigation_outside_project_is_blocked(session: BrowserSession) -> None:
    with pytest.raises(ToolError, match="outside the project"):
        await session.goto("https://evil.example/")
    assert (await session.goto("/cart")).url == SITE + "/cart"  # relative URLs resolve against the site


async def test_clicking_an_external_link_navigates_back(session: BrowserSession) -> None:
    snap = await session.snapshot()
    elsewhere = next(e.ref for e in snap.elements if e.name == "Elsewhere")

    with pytest.raises(ToolError, match="left the project site"):
        await session.click(elsewhere)

    assert session.url == SITE + "/"


async def test_screenshot_writes_png(session: BrowserSession, tmp_path) -> None:  # noqa: ANN001
    path = tmp_path / "shots" / "home.png"
    data = await session.screenshot(path)
    assert data.startswith(b"\x89PNG") and path.read_bytes() == data


async def test_execute_runs_validated_tool_calls(session: BrowserSession) -> None:
    adapter = TypeAdapter(ToolCall)
    snap = await session.snapshot()
    name_ref = next(e.ref for e in snap.elements if e.name == "Your name")

    typed = await session.execute(adapter.validate_python({"tool": "type", "ref": name_ref, "text": "Asha"}))
    shot = await session.execute(adapter.validate_json('{"tool": "snapshot"}'))
    done = await session.execute(adapter.validate_python({"tool": "done", "result": "pass", "reason": "ok"}))

    assert typed.ok and 'value="Asha"' in shot.message and done.message.startswith("Done: pass")
    with pytest.raises(Exception):
        adapter.validate_python({"tool": "rm -rf", "ref": 1})


async def test_launcher_blocks_internal_hosts_including_redirects_and_page_scripts(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import get_settings
    from worker.browser.session import BrowserLauncher

    # Block localhost too for this test: a redirect to localhost:59999 then fails as "name not resolved" (blocked)
    # instead of "connection refused" (reached), which proves the block on any machine.
    monkeypatch.setattr(get_settings(), "blocked_target_hosts", get_settings().blocked_target_hosts + ",localhost")

    async def site(route: Route) -> None:
        if route.request.url.endswith("/go-internal"):
            await route.fulfill(status=302, headers={"Location": "http://localhost:59999/"})
        else:
            await route.fulfill(status=200, content_type="text/html", body="<title>t</title><p>hi</p>")

    try:
        launcher_cm = BrowserLauncher.start(channel=os.getenv("BROWSER_CHANNEL") or None)
        launcher = await launcher_cm.__aenter__()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"No browser available ({str(exc).splitlines()[0]})")
    try:
        async with launcher.session(SITE + "/", action_delay_ms=0, timeout_ms=5000) as s:
            failures: list[str] = []
            s.page.on("requestfailed", lambda r: failures.append(f"{r.url} {r.failure}"))
            await s.page.route(f"{SITE}/**", site)
            await s.goto("/")
            await s.page.evaluate("""async () => {
                for (const u of ['http://169.254.169.254/latest/meta-data/', 'http://mongo:27017/']) {
                    try { await fetch(u, { mode: 'no-cors' }) } catch (e) {}
                }
            }""")
            # Playwright doesn't intercept redirect targets: the resolver rules must stop this one.
            with pytest.raises(ToolError, match="ERR_NAME_NOT_RESOLVED"):
                await s.goto("/go-internal")
    finally:
        await launcher_cm.__aexit__(None, None, None)

    assert any(f.startswith("http://169.254.169.254/") and "BLOCKED_BY_CLIENT" in f for f in failures)
    assert any(f.startswith("http://mongo:27017/") and "BLOCKED_BY_CLIENT" in f for f in failures)


def test_browser_args_make_blocked_hosts_unresolvable() -> None:
    from app.core.targets import browser_args

    (arg,) = browser_args()
    assert arg.startswith("--host-resolver-rules=")
    assert "MAP mongo ~NOTFOUND" in arg and "MAP api ~NOTFOUND" in arg and "MAP 169.254.* ~NOTFOUND" in arg
    assert "demo-shop" not in arg and "localhost" not in arg
