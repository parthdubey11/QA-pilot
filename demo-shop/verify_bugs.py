"""Prove every planted defect in GROUND_TRUTH.md exists in the running demo shop.

One test per functional bug (F01-F15) and per accessibility violation (A01-A10). Each test follows the
reproduction steps in GROUND_TRUTH.md through a real browser and PASSES when the defect is present.
At the end it prints a table: ID | name | CONFIRMED / MISSING.

Run (demo shop must be up, e.g. `docker compose up -d demo-shop`):
    pip install -r requirements-verify.txt
    playwright install chromium        # or use an installed browser: BROWSER_CHANNEL=msedge / chrome
    python verify_bugs.py              # same as: pytest verify_bugs.py

Env: DEMO_SHOP_URL (default http://localhost:8080), BROWSER_CHANNEL (optional), HEADED=1 to watch.
"""

import functools
import json
import os
import sys
import urllib.request
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Browser, Page, expect, sync_playwright

BASE = os.getenv("DEMO_SHOP_URL", "http://localhost:8080").rstrip("/")
AXE_JS = Path(__file__).parent / "tests" / "vendor" / "axe.min.js"  # axe-core 4.13.0 (MPL-2.0)
SEED_EMAIL, SEED_PASSWORD = "demo@shop.test", "demo1234"
QUIET_WAIT_MS = 1200  # how long to watch for an update that should happen but doesn't

DEFECTS: dict[str, str] = {
    "F01": "Cart accepts negative quantity",
    "F02": "Duplicate email signup allowed",
    "F03": "Total not updated after removing item",
    "F04": "Contact form accepts invalid email",
    "F05": "Logout doesn't clear session",
    "F06": "Sale price not applied in cart",
    "F07": "Checkout allowed with empty cart",
    "F08": "Password shorter than minimum accepted",
    "F09": "Out-of-stock product can be added",
    "F10": "Header cart count not updated after add",
    "F11": "Non-numeric product URL shows raw JSON",
    "F12": "Login from checkout doesn't return there",
    "F13": "Mixed-case email can't log in",
    "F14": "Can order more than available stock",
    "F15": "Price low-to-high sort is wrong",
    "A01": "Missing lang attribute (/contact)",
    "A02": "Product images missing alt (/)",
    "A03": "Signup email field has no label",
    "A04": "Icon-only remove button has no name",
    "A05": "Low-contrast product description",
    "A06": "Keyboard trap in promo code field",
    "A07": "Icon-only cart link has no name",
    "A08": "Login page has no <title>",
    "A09": "Zoom disabled by meta viewport",
    "A10": "No visible focus indicator on buttons",
}
RESULTS: dict[str, str] = {}


def defect(defect_id: str) -> Callable[[Callable[..., None]], Callable[..., None]]:
    """Record CONFIRMED if the test passes (defect present), MISSING if it fails."""

    def decorator(fn: Callable[..., None]) -> Callable[..., None]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> None:
            try:
                fn(*args, **kwargs)
            except BaseException:
                RESULTS[defect_id] = "MISSING"
                raise
            RESULTS[defect_id] = "CONFIRMED"

        return wrapper

    return decorator


# ---------- fixtures & helpers ----------


def shop_post(path: str, body: dict | None = None) -> None:
    req = urllib.request.Request(f"{BASE}{path}", data=json.dumps(body or {}).encode(), method="POST",
                                 headers={"Content-Type": "application/json",
                                          "X-Reset-Token": os.getenv("DEMO_SHOP_RESET_TOKEN", "")})
    with urllib.request.urlopen(req, timeout=10) as res:
        res.read()


@pytest.fixture(scope="session", autouse=True)
def results_table(request: pytest.FixtureRequest) -> Iterator[None]:
    yield
    tr = request.config.pluginmanager.get_plugin("terminalreporter")
    write = tr.write_line if tr else print
    ran = [d for d in DEFECTS if d in RESULTS]
    write("")
    write(f"{'ID':<4} {'Defect':<44} Result")
    write(f"{'-' * 4} {'-' * 44} {'-' * 9}")
    for defect_id, name in DEFECTS.items():
        write(f"{defect_id:<4} {name:<44} {RESULTS.get(defect_id, 'NOT RUN')}")
    confirmed = sum(RESULTS[d] == "CONFIRMED" for d in ran)
    write(f"\n{confirmed}/{len(ran)} planted defects confirmed ({len(DEFECTS)} documented)")


@pytest.fixture(scope="session")
def browser() -> Iterator[Browser]:
    try:
        shop_post("/reset")
    except OSError as exc:
        pytest.exit(f"Demo shop not reachable at {BASE}: {exc}", returncode=2)
    with sync_playwright() as p:
        b = p.chromium.launch(channel=os.getenv("BROWSER_CHANNEL") or None, headless=os.getenv("HEADED") != "1")
        yield b
        b.close()


@pytest.fixture
def page(browser: Browser) -> Iterator[Page]:
    shop_post("/reset")
    shop_post("/admin/ui-variant", {"variant": "v1"})  # steps below use the v1 labels
    context = browser.new_context(base_url=BASE)
    pg = context.new_page()
    pg.set_default_timeout(8000)
    yield pg
    context.close()
    shop_post("/reset")


def api_login(page: Page) -> None:
    """Log in through the API (shares cookies with the page) for tests where login isn't the subject."""
    assert page.request.post("/api/login", data={"email": SEED_EMAIL, "password": SEED_PASSWORD}).ok


def api_add_to_cart(page: Page, product_id: int, quantity: int = 1) -> None:
    assert page.request.post("/api/cart/items", data={"product_id": product_id, "quantity": quantity}).ok


def ui_login(page: Page, email: str = SEED_EMAIL, password: str = SEED_PASSWORD) -> None:
    page.get_by_label("Email", exact=True).fill(email)
    page.get_by_label("Password").fill(password)
    page.get_by_role("button", name="Log in", exact=True).click()


def ui_signup(page: Page, name: str, email: str, password: str) -> None:
    page.goto("/signup")
    page.get_by_label("Full name").fill(name)
    page.locator("#signup-email").fill(email)  # unlabelled field (A03), so no get_by_label
    page.get_by_label("Password").fill(password)
    page.get_by_role("button", name="Create account").click()


def open_product(page: Page, product_id: int) -> None:
    page.goto(f"/products/{product_id}")
    expect(page.locator("#product-detail")).to_be_visible()


def click_add_to_cart(page: Page, quantity: int = 1) -> None:
    page.get_by_role("button", name="Add to cart").click()
    expect(page.get_by_text(f"Added {quantity} to your cart.")).to_be_visible()


def set_cart_quantity(page: Page, product_name: str, quantity: int) -> None:
    box = page.get_by_label(f"Quantity for {product_name}")
    box.fill(str(quantity))
    box.press("Enter")


def fill_checkout_and_place_order(page: Page) -> None:
    page.get_by_label("Full name").fill("Demo User")
    page.get_by_label("Street address").fill("1 Main St")
    page.get_by_label("City").fill("Pune")
    page.get_by_label("Country").select_option("India")
    page.get_by_label("Card number (test)").fill("4242424242424242")
    page.get_by_role("button", name="Place order").click()


def axe_targets(page: Page, rule: str) -> list[str]:
    """Run a single axe-core rule on the current page and return the CSS targets that violate it."""
    page.wait_for_load_state("networkidle")
    page.add_script_tag(path=str(AXE_JS))
    result = page.evaluate("rule => axe.run(document, {runOnly: {type: 'rule', values: [rule]}})", rule)
    return [str(t) for v in result["violations"] for node in v["nodes"] for t in node["target"]]


# ---------- functional bugs ----------


@defect("F01")
def test_f01_cart_accepts_negative_quantity(page: Page) -> None:
    open_product(page, 3)
    click_add_to_cart(page)
    page.goto("/cart")
    set_cart_quantity(page, "Steel Water Bottle", -2)
    expect(page.locator("#cart-total")).to_have_text("-$39.98")


@defect("F02")
def test_f02_duplicate_email_signup_allowed(page: Page) -> None:
    ui_signup(page, "Twin", SEED_EMAIL, "password1")
    expect(page.get_by_text("Account created.")).to_be_visible()


@defect("F03")
def test_f03_total_not_updated_after_remove(page: Page) -> None:
    api_add_to_cart(page, 1)
    api_add_to_cart(page, 3)
    page.goto("/cart")
    expect(page.locator("#cart-total")).to_have_text("$109.98")
    bottle_row = page.get_by_role("row").filter(has_text="Steel Water Bottle")
    bottle_row.get_by_role("button").click()
    expect(bottle_row).to_have_count(0)
    page.wait_for_timeout(QUIET_WAIT_MS)
    expect(page.locator("#cart-total")).to_have_text("$109.98")  # should be $89.99
    page.reload()
    expect(page.locator("#cart-total")).to_have_text("$89.99")  # server knew all along


@defect("F04")
def test_f04_contact_accepts_invalid_email(page: Page) -> None:
    page.goto("/contact")
    page.get_by_label("Your name").fill("Test")
    page.get_by_label("Email").fill("not-an-email")
    page.get_by_label("Message").fill("Hello")
    page.get_by_role("button", name="Send message").click()
    expect(page.get_by_text("Thanks! We'll get back to you")).to_be_visible()


@defect("F05")
def test_f05_logout_does_not_clear_session(page: Page) -> None:
    page.goto("/login")
    ui_login(page)
    page.wait_for_url(f"{BASE}/")
    expect(page.get_by_text("Hi, Demo User")).to_be_visible()
    page.get_by_role("button", name="Log out").click()
    page.wait_for_load_state("networkidle")
    expect(page.get_by_text("Hi, Demo User")).to_be_visible()
    expect(page.get_by_role("link", name="Log in")).to_be_hidden()
    page.goto("/checkout")
    page.wait_for_timeout(QUIET_WAIT_MS)
    assert page.url == f"{BASE}/checkout", "a logged-out user should be sent to /login"


@defect("F06")
def test_f06_sale_price_not_applied_in_cart(page: Page) -> None:
    open_product(page, 2)
    expect(page.locator("#product-price")).to_contain_text("$39.99")
    click_add_to_cart(page)
    page.goto("/cart")
    expect(page.get_by_role("row").filter(has_text="Canvas Backpack")).to_contain_text("$49.99")
    expect(page.locator("#cart-total")).to_have_text("$49.99")


@defect("F07")
def test_f07_checkout_with_empty_cart(page: Page) -> None:
    api_login(page)
    page.goto("/cart")
    expect(page.get_by_text("Your cart is empty.")).to_be_visible()
    page.get_by_role("link", name="Checkout", exact=True).click()
    page.wait_for_url(f"{BASE}/checkout")
    fill_checkout_and_place_order(page)
    expect(page.get_by_text("Total charged: $0.00")).to_be_visible()


@defect("F08")
def test_f08_short_password_accepted(page: Page) -> None:
    ui_signup(page, "Short", "short@example.com", "abc")
    expect(page.get_by_text("At least 8 characters.")).to_be_visible()
    expect(page.get_by_text("Account created.")).to_be_visible()


@defect("F09")
def test_f09_out_of_stock_can_be_added(page: Page) -> None:
    open_product(page, 4)
    expect(page.get_by_text("Out of stock")).to_be_visible()
    expect(page.get_by_role("button", name="Add to cart")).to_be_enabled()
    click_add_to_cart(page)
    page.goto("/cart")
    expect(page.get_by_role("row").filter(has_text="Wool Beanie")).to_be_visible()


@defect("F10")
def test_f10_header_count_not_updated(page: Page) -> None:
    open_product(page, 1)
    expect(page.locator("#cart-count")).to_have_text("0")
    click_add_to_cart(page)
    page.wait_for_timeout(QUIET_WAIT_MS)
    expect(page.locator("#cart-count")).to_have_text("0")  # should be 1
    page.reload()
    expect(page.locator("#cart-count")).to_have_text("1")


@defect("F11")
def test_f11_non_numeric_product_url_raw_json(page: Page) -> None:
    response = page.goto("/products/abc")
    assert response is not None and response.status == 422
    expect(page.locator("body")).to_contain_text('"int_parsing"')
    expect(page.get_by_role("navigation")).to_have_count(0)


@defect("F12")
def test_f12_login_from_checkout_goes_home(page: Page) -> None:
    page.goto("/checkout")
    page.wait_for_url("**/login?next=/checkout")
    ui_login(page)
    page.wait_for_load_state("networkidle")
    expect(page).to_have_url(f"{BASE}/")  # should be /checkout


@defect("F13")
def test_f13_mixed_case_email_cannot_log_in(page: Page) -> None:
    ui_signup(page, "Case User", "Case.User@Example.com", "password1")
    expect(page.get_by_text("Account created.")).to_be_visible()
    page.goto("/login")
    ui_login(page, "Case.User@Example.com", "password1")
    expect(page.get_by_text("Invalid email or password.")).to_be_visible()


@defect("F14")
def test_f14_order_more_than_stock(page: Page) -> None:
    api_login(page)
    open_product(page, 2)
    expect(page.get_by_text("In stock (8 left)")).to_be_visible()
    click_add_to_cart(page)
    page.goto("/cart")
    set_cart_quantity(page, "Canvas Backpack", 20)
    expect(page.locator("#cart-total")).to_have_text("$999.80")
    page.get_by_role("link", name="Checkout", exact=True).click()
    page.wait_for_url(f"{BASE}/checkout")
    fill_checkout_and_place_order(page)
    expect(page.get_by_text("is confirmed. Total charged: $999.80.")).to_be_visible()


@defect("F15")
def test_f15_price_sort_is_lexicographic(page: Page) -> None:
    page.goto("/")
    expect(page.locator(".product-card")).to_have_count(6)
    page.get_by_label("Sort by").select_option(label="Price: low to high")
    shown = [p.split()[0] for p in page.locator(".product-card .price").all_inner_texts()]
    assert shown[0] == "$109.99", shown
    as_numbers = [float(p.lstrip("$")) for p in shown]
    assert as_numbers != sorted(as_numbers), shown


# ---------- accessibility violations ----------


@defect("A01")
def test_a01_contact_missing_lang(page: Page) -> None:
    page.goto("/contact")
    assert page.locator("html").get_attribute("lang") is None
    assert axe_targets(page, "html-has-lang") == ["html"]


@defect("A02")
def test_a02_product_images_missing_alt(page: Page) -> None:
    page.goto("/")
    expect(page.locator(".product-card img")).to_have_count(6)
    assert len(axe_targets(page, "image-alt")) == 6


@defect("A03")
def test_a03_signup_email_unlabelled(page: Page) -> None:
    page.goto("/signup")
    assert axe_targets(page, "label") == ["#signup-email"]


@defect("A04")
def test_a04_remove_button_has_no_name(page: Page) -> None:
    api_add_to_cart(page, 1)
    page.goto("/cart")
    expect(page.locator(".remove-btn")).to_have_count(1)
    targets = axe_targets(page, "button-name")
    assert len(targets) == 1 and "remove-btn" in targets[0], targets


@defect("A05")
def test_a05_low_contrast_description(page: Page) -> None:
    open_product(page, 1)
    assert axe_targets(page, "color-contrast") == ["#product-description"]


@defect("A06")
def test_a06_keyboard_trap_in_promo_code(page: Page) -> None:
    api_login(page)
    page.goto("/checkout")
    page.get_by_label("Promo code (optional)").click()
    for key in ["Tab", "Tab", "Tab", "Shift+Tab", "Shift+Tab"]:
        page.keyboard.press(key)
        assert page.evaluate("document.activeElement.id") == "promo_code", f"focus escaped after {key}"


@defect("A07")
def test_a07_cart_link_has_no_name(page: Page) -> None:
    page.goto("/")
    assert axe_targets(page, "link-name") == [".cart-link"]


@defect("A08")
def test_a08_login_has_no_title(page: Page) -> None:
    page.goto("/login")
    assert page.title() == ""
    assert axe_targets(page, "document-title") == ["html"]


@defect("A09")
def test_a09_zoom_disabled(page: Page) -> None:
    open_product(page, 1)
    assert "user-scalable=no" in (page.locator('meta[name="viewport"]').get_attribute("content") or "")
    assert len(axe_targets(page, "meta-viewport")) == 1


@defect("A10")
def test_a10_no_focus_indicator_on_buttons(page: Page) -> None:
    open_product(page, 1)
    page.get_by_label("Quantity").focus()
    page.keyboard.press("Tab")  # keyboard focus, so :focus-visible applies
    focused = page.evaluate("""() => {
        const el = document.activeElement, s = getComputedStyle(el)
        return {action: el.dataset.action, outline: s.outlineStyle, shadow: s.boxShadow}
    }""")
    assert focused == {"action": "add-to-cart", "outline": "none", "shadow": "none"}, focused


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q", "-p", "no:cacheprovider", *sys.argv[1:]]))
