# Demo Shop — Ground Truth

Planted defects used by `eval/` to measure QA Pilot's recall and precision.
**Never put this file (or the demo-shop source) into an LLM prompt.** QA Pilot must find these by using the site.

- 15 functional bugs (F01–F15) and 10 accessibility violations (A01–A10).
- Everything else is intended to work correctly and pass axe-core cleanly. Any other reported bug counts as a
  false positive, unless it's a new real defect (then fix it or add it here).
- `python verify_bugs.py` proves every item below still exists (one Playwright test each; prints
  ID / name / CONFIRMED or MISSING). `pytest` (in `tests/`) pins the server-side bugs too.

## Before you start

- Shop URL: http://localhost:8080 (`docker compose up -d demo-shop`).
- Reset before every run: `curl -X POST http://localhost:8080/reset` (restores seed data and default labels).
- Seed login: `demo@shop.test` / `demo1234`.
- Steps below use the default **v1** labels (see "Self-healing demo" at the end for v2).
- Seed products:

| # | Product | Price shown | Stock |
|---|---|---|---|
| 1 | Trail Runner Shoes | $89.99 | 12 |
| 2 | Canvas Backpack | **$39.99** (sale, was $49.99) | 8 |
| 3 | Steel Water Bottle | $19.99 | 30 |
| 4 | Wool Beanie | $24.99 | **0 (out of stock)** |
| 5 | Rain Jacket | **$109.99** (sale, was $129.99) | 5 |
| 6 | Travel Mug | $14.99 | 20 |

## Summary

| ID | Defect | Where | Expected detection |
|---|---|---|---|
| F01 | Cart accepts negative quantity | `/cart` quantity box | Planner edge case "invalid quantity" |
| F02 | Duplicate email signup allowed | `/signup` | Planner edge case "existing email" |
| F03 | Total not updated after removing an item | `/cart` remove button | Judge checks total vs. remaining lines |
| F04 | Contact form accepts invalid email | `/contact` | Planner edge case "invalid email" |
| F05 | Logout doesn't clear the session | Header **Log out** | Judge checks header after logout |
| F06 | Sale price not applied in cart | `/cart` | Judge compares product-page vs. cart price |
| F07 | Checkout allowed with an empty cart | `/cart` → `/checkout` | Planner edge case "empty cart checkout" |
| F08 | Password shorter than stated minimum accepted | `/signup` | Planner edge case "short password" |
| F09 | Out-of-stock product can be added | `/products/4` | Judge: "Out of stock" item added |
| F10 | Header cart count not updated after adding | Product page + header badge | Judge checks badge after add |
| F11 | Non-numeric product URL shows raw JSON | `/products/abc` | Explorer/Planner "invalid URL" |
| F12 | Login from checkout doesn't return to checkout | `/checkout` → `/login` | Judge compares landing page with goal |
| F13 | Mixed-case email can't log in after signup | `/signup` → `/login` | Planner "sign up then log in" |
| F14 | Can order more than available stock | `/cart` → `/checkout` | Planner edge case "quantity above stock" |
| F15 | "Price: low to high" sort is wrong | `/` Sort by | Judge checks prices ascend |
| A01 | Missing `lang` on `<html>` | `/contact` | axe `html-has-lang` |
| A02 | Images without `alt` | `/` product grid | axe `image-alt` |
| A03 | Form field without label | `/signup` email | axe `label` |
| A04 | Icon-only button without name | `/cart` remove | axe `button-name` |
| A05 | Low text contrast | `/products/{id}` description | axe `color-contrast` |
| A06 | Keyboard trap | `/checkout` promo code | **Keyboard check only** |
| A07 | Icon-only link without name | Header cart icon | axe `link-name` |
| A08 | Missing page `<title>` | `/login` | axe `document-title` |
| A09 | Zoom disabled | `/products/{id}` meta viewport | axe `meta-viewport` |
| A10 | No visible focus indicator | Every `.btn` button | **Keyboard/visual check only** |

axe-core alone (WCAG 2.x A/AA tags) can find at most **8/10** accessibility items: A06 and A10 need keyboard
testing. Running axe-core 4.13 over all 7 pages reports exactly these 8 rules and nothing else, even with all
rules enabled.

---

## Functional bugs

### F01 — Cart accepts a negative quantity
- **Location:** quantity box on `/cart` → `PATCH /api/cart/items/{id}` (`update_cart_item` in `app/main.py`; `add_to_cart` also accepts any integer). The product page's box is protected by the browser's `min="1"` check.
- **Steps:**
  1. Open http://localhost:8080/products/3 (Steel Water Bottle).
  2. Click **Add to cart**. You see "Added 1 to your cart."
  3. Click the cart icon (top right) to open `/cart`.
  4. In the box **Quantity for Steel Water Bottle**, type `-2` and press **Enter**.
- **You should see:** an error, or the quantity reset to 1; total stays **$19.99**.
- **What happens:** Subtotal and **Total** become **-$39.98**.
- **Expected detection:** Planner edge case "invalid quantity"; Judge flags a negative total.
- **Verified by:** `test_f01_cart_accepts_negative_quantity`

### F02 — Duplicate email signup allowed
- **Location:** `POST /api/signup` (`signup`: no uniqueness check).
- **Steps:**
  1. Open http://localhost:8080/signup.
  2. Full name: `Twin`. Email: `demo@shop.test` (already registered). Password: `password1`.
  3. Click **Create account**.
- **You should see:** "An account with this email already exists."
- **What happens:** "Account created. You can now log in."
- **Expected detection:** Planner edge case "sign up with an existing email".
- **Verified by:** `test_f02_duplicate_email_signup_allowed`

### F03 — Total not updated after removing an item
- **Location:** `/cart`, remove handler in `static/js/cart.js` (removes the row, never re-renders the total).
- **Steps:**
  1. Open `/products/1` (Trail Runner Shoes) and click **Add to cart**.
  2. Open `/products/3` (Steel Water Bottle) and click **Add to cart**.
  3. Open `/cart`. Total shows **$109.98**.
  4. In the Steel Water Bottle row, click the trash-can button (last column).
- **You should see:** the row disappears and Total becomes **$89.99**.
- **What happens:** the row disappears but Total still shows **$109.98**. After a page reload it shows $89.99.
- **Expected detection:** Executor removes an item; Judge compares the remaining line with the total.
- **Verified by:** `test_f03_total_not_updated_after_remove`

### F04 — Contact form accepts an invalid email
- **Location:** `/contact` (email input is `type="text"`; `POST /api/contact` only checks it's non-empty).
- **Steps:**
  1. Open http://localhost:8080/contact.
  2. Your name: `Test`. Email: `not-an-email`. Message: `Hello`.
  3. Click **Send message**.
- **You should see:** "Please enter a valid email address."
- **What happens:** "Thanks! We'll get back to you within 2 working days."
- **Expected detection:** Planner edge case "invalid email format".
- **Verified by:** `test_f04_contact_accepts_invalid_email`

### F05 — Logout doesn't clear the session
- **Location:** `POST /api/logout` in `app/main.py`: deletes the cookie with the wrong `path=/api` (it was set with `path=/`) and never removes the server session.
- **Steps:**
  1. Open http://localhost:8080/login. Email `demo@shop.test`, Password `demo1234`, click **Log in**.
  2. The header shows "Hi, Demo User **Log out**".
  3. Click **Log out**.
  4. Open http://localhost:8080/checkout.
- **You should see:** the header shows **Log in** / **Sign up**; `/checkout` redirects to `/login`.
- **What happens:** the header still shows "Hi, Demo User"; `/checkout` opens the checkout form.
- **Expected detection:** Judge checks the header after logout.
- **Verified by:** `test_f05_logout_does_not_clear_session`

### F06 — Sale price not applied in the cart
- **Location:** `cart_summary` in `app/main.py` uses `price` instead of `sale_price`.
- **Steps:**
  1. Open http://localhost:8080/products/2 (Canvas Backpack). Price shows **$39.99** ~~$49.99~~.
  2. Click **Add to cart**.
  3. Open `/cart`.
- **You should see:** Price and Total **$39.99**.
- **What happens:** Price and Total **$49.99**.
- **Expected detection:** Judge compares the product-page price with the cart price.
- **Verified by:** `test_f06_sale_price_not_applied_in_cart`

### F07 — Checkout allowed with an empty cart
- **Location:** `/cart` always shows **Checkout**; `POST /api/checkout` has no empty-cart check.
- **Steps:**
  1. Log in as `demo@shop.test` / `demo1234` (cart is empty after reset).
  2. Open `/cart`. It says "Your cart is empty." and the **Checkout** button is still there. Click it.
  3. Full name `Demo User`, Street address `1 Main St`, City `Pune`, Country `India`, Card number (test) `4242424242424242`.
  4. Click **Place order**.
- **You should see:** "Your cart is empty"; no order is created.
- **What happens:** "Thank you! Order #1001 is confirmed. Total charged: $0.00."
- **Expected detection:** Planner edge case "checkout with an empty cart". *Note:* the executor may stop at the card field ("blocked: needs human", per the payment safety rule); the eval should count that as a miss.
- **Verified by:** `test_f07_checkout_with_empty_cart`

### F08 — Password shorter than the stated minimum accepted
- **Location:** `/signup` hint says "At least 8 characters."; `signup` in `app/main.py` only rejects an empty password.
- **Steps:**
  1. Open http://localhost:8080/signup.
  2. Full name `Short`, Email `short@example.com`, Password `abc`.
  3. Click **Create account**.
- **You should see:** "Password must be at least 8 characters."
- **What happens:** "Account created. You can now log in."
- **Expected detection:** Planner edge case "weak/short password".
- **Verified by:** `test_f08_short_password_accepted`

### F09 — Out-of-stock product can be added to the cart
- **Location:** `/products/4`: "Out of stock" is shown but the button stays enabled; `add_to_cart` has no stock check.
- **Steps:**
  1. Open http://localhost:8080/products/4 (Wool Beanie). It says **Out of stock**.
  2. Click **Add to cart**.
  3. Open `/cart`.
- **You should see:** the button disabled (or an error) and nothing added.
- **What happens:** "Added 1 to your cart."; Wool Beanie ($24.99) is in the cart.
- **Expected detection:** Judge notices an out-of-stock item was added.
- **Verified by:** `test_f09_out_of_stock_can_be_added`

### F10 — Header cart count not updated after adding
- **Location:** submit handler in `static/js/product.js` never calls `refreshCartCount()`.
- **Steps:**
  1. Open http://localhost:8080/products/1. The red badge next to the cart icon shows **0**.
  2. Click **Add to cart**. You see "Added 1 to your cart."
- **You should see:** the badge changes to **1**.
- **What happens:** the badge stays **0** until the page is reloaded.
- **Expected detection:** Judge checks the header badge after adding.
- **Verified by:** `test_f10_header_count_not_updated`

### F11 — Non-numeric product URL shows a raw JSON error
- **Location:** `GET /products/{product_id}` is typed `int`, so FastAPI answers HTTP 422 JSON. (`/products/999` correctly shows "Sorry, we couldn't find that product.")
- **Steps:**
  1. Open http://localhost:8080/products/abc.
- **You should see:** the shop page with header and a friendly "product not found" message.
- **What happens:** a bare page of JSON: `{"detail":[{"type":"int_parsing","loc":["path","product_id"],...}]}`, with no header or navigation.
- **Expected detection:** Explorer/Planner edge case "invalid product URL".
- **Verified by:** `test_f11_non_numeric_product_url_raw_json`

### F12 — Login from checkout doesn't return to checkout
- **Location:** `static/js/checkout.js` redirects to `/login?next=/checkout`, but `static/js/login.js` reads `?redirect=`.
- **Steps:**
  1. While logged out, open http://localhost:8080/checkout. You're sent to `/login?next=/checkout`.
  2. Email `demo@shop.test`, Password `demo1234`, click **Log in**.
- **You should see:** you're back on `/checkout`.
- **What happens:** you land on the home page `/`.
- **Expected detection:** Judge compares the landing page with the goal ("continue to checkout after login").
- **Verified by:** `test_f12_login_from_checkout_goes_home`

### F13 — Accounts with capital letters in the email can't log in
- **Location:** `login` in `app/main.py` lower-cases the typed email but compares it with the email exactly as stored at signup.
- **Steps:**
  1. Open `/signup`. Full name `Case User`, Email `Case.User@Example.com`, Password `password1`. Click **Create account** → "Account created."
  2. Open `/login`. Email `Case.User@Example.com`, Password `password1`. Click **Log in**.
- **You should see:** logged in ("Hi, Case User").
- **What happens:** "Invalid email or password."
- **Expected detection:** Planner happy path "sign up then log in" with a mixed-case email.
- **Verified by:** `test_f13_mixed_case_email_cannot_log_in`

### F14 — Can order more than the available stock
- **Location:** `add_to_cart` / `update_cart_item` have no stock check; `checkout` silently clamps stock to 0.
- **Steps:**
  1. Log in as `demo@shop.test` / `demo1234`.
  2. Open `/products/2` (Canvas Backpack). It says **In stock (8 left)**. Click **Add to cart**.
  3. Open `/cart`. In **Quantity for Canvas Backpack** type `20` and press **Enter**. Total **$999.80**.
  4. Click **Checkout**, fill the form as in F07, click **Place order**.
- **You should see:** "Only 8 available" and the order refused.
- **What happens:** "Order #1001 is confirmed. Total charged: $999.80."
- **Expected detection:** Planner edge case "quantity above stock".
- **Verified by:** `test_f14_order_more_than_stock`

### F15 — "Price: low to high" sort is wrong
- **Location:** `static/js/index.js` sorts the formatted price strings with `localeCompare`, so "$109.99" sorts before "$14.99".
- **Steps:**
  1. Open http://localhost:8080/.
  2. In **Sort by**, choose **Price: low to high**.
- **You should see:** $14.99, $19.99, $24.99, $39.99, $89.99, $109.99.
- **What happens:** **$109.99** (Rain Jacket), $14.99, $19.99, $24.99, $39.99, $89.99.
- **Expected detection:** Judge reads the prices in order and checks they ascend.
- **Verified by:** `test_f15_price_sort_is_lexicographic`

---

## Accessibility violations

"axe rule" = axe-core rule id, reported with tags `wcag2a, wcag2aa, wcag21a, wcag21aa, wcag22aa`.

### A01 — Page has no `lang` attribute (WCAG 3.1.1, A)
- **Location:** `/contact`, `pages/contact.html` (`<html>` has no `lang`).
- **Steps:** Open http://localhost:8080/contact, then DevTools → Elements: the root element is `<html>` (other pages have `<html lang="en">`). Or run axe DevTools.
- **You should see:** `<html lang="en">`. **What happens:** no `lang`, so screen readers guess the pronunciation language.
- **Expected detection:** axe `html-has-lang` (target `html`). **Verified by:** `test_a01_contact_missing_lang`

### A02 — Product images have no `alt` (WCAG 1.1.1, A)
- **Location:** `/` product grid (6 `<img>` rendered by `static/js/index.js`).
- **Steps:** Open http://localhost:8080/ and inspect any product image: `<img src="/static/img/…svg" width="300" height="200">` has no `alt`.
- **You should see:** `alt="Trail Runner Shoes"` (or `alt=""` if decorative). **What happens:** no `alt` on any of the 6 images.
- **Expected detection:** axe `image-alt` (6 nodes, one violation). **Verified by:** `test_a02_product_images_missing_alt`

### A03 — Form field without a label (WCAG 1.3.1 / 4.1.2, A)
- **Location:** `/signup` email input: "Email" is a plain `<div class="field-label">`, not a `<label for>`.
- **Steps:** Open http://localhost:8080/signup and click the text "Email": focus does **not** move into the field (it does for "Full name" and "Password"). A screen reader announces just "edit text".
- **You should see:** a `<label for="signup-email">`. **What happens:** the field has no accessible name.
- **Expected detection:** axe `label` (target `#signup-email`). **Verified by:** `test_a03_signup_email_unlabelled`

### A04 — Icon-only button with no accessible name (WCAG 4.1.2, A)
- **Location:** `/cart`, trash-can "remove" button on each row (`static/js/cart.js`; SVG is `aria-hidden`, no text or `aria-label`).
- **Steps:** Add any product, open `/cart`, Tab to the trash-can button: a screen reader says just "button".
- **You should see:** a name like "Remove Trail Runner Shoes". **What happens:** empty name.
- **Expected detection:** axe `button-name` (target `.remove-btn`). **Verified by:** `test_a04_remove_button_has_no_name`

### A05 — Low text contrast (WCAG 1.4.3, AA)
- **Location:** product description on `/products/{id}` (`.product-description { color: #b8b8b8 }` on white, about 2:1).
- **Steps:** Open http://localhost:8080/products/1: the paragraph under the price is pale grey.
- **You should see:** a contrast ratio of at least 4.5:1. **What happens:** about 2:1.
- **Expected detection:** axe `color-contrast` (target `#product-description`). **Verified by:** `test_a05_low_contrast_description`

### A06 — Keyboard trap (WCAG 2.1.2, A)
- **Location:** `/checkout` promo code field: the `keydown` handler in `static/js/checkout.js` swallows Tab.
- **Steps:**
  1. Log in and open http://localhost:8080/checkout.
  2. Click in **Promo code (optional)**.
  3. Press **Tab** (several times), then **Shift+Tab**.
- **You should see:** focus moves on to **Place order** (and back to Card number with Shift+Tab).
- **What happens:** focus never leaves the promo code field, so keyboard-only users can't reach **Place order**.
- **Expected detection:** **keyboard-navigation check only** (axe can't detect it). **Verified by:** `test_a06_keyboard_trap_in_promo_code`

### A07 — Icon-only link with no accessible name (WCAG 2.4.4 / 4.1.2, A)
- **Location:** header cart icon link on every page (`pages/_header.html`: SVG is `aria-hidden`, no text).
- **Steps:** On any page, Tab to the cart icon (top right): a screen reader says just "link".
- **You should see:** a name like "Cart (2 items)". **What happens:** empty name.
- **Expected detection:** axe `link-name` (target `.cart-link`). **Verified by:** `test_a07_cart_link_has_no_name`

### A08 — Page has no `<title>` (WCAG 2.4.2, A)
- **Location:** `/login` (`pages/login.html` has no `<title>`).
- **Steps:** Open http://localhost:8080/login and look at the browser tab: it shows the URL instead of a page name.
- **You should see:** e.g. "Log in | Demo Shop". **What happens:** no title.
- **Expected detection:** axe `document-title`. **Verified by:** `test_a08_login_has_no_title`

### A09 — Zoom disabled (WCAG 1.4.4, AA)
- **Location:** `/products/{id}`, `<meta name="viewport" content="…, maximum-scale=1, user-scalable=no">` in `pages/product.html`.
- **Steps:** Open a product page on a phone (or DevTools device mode) and pinch-zoom: it won't zoom.
- **You should see:** zoom allowed. **What happens:** zoom blocked.
- **Expected detection:** axe `meta-viewport`. **Verified by:** `test_a09_zoom_disabled`

### A10 — No visible focus indicator on buttons (WCAG 2.4.7, AA)
- **Location:** `static/style.css`: `.btn:focus, .btn:focus-visible { outline: none; }` with no replacement style. Affects every `.btn` (Add to cart, Place order, Create account, Log in, Send message, Checkout).
- **Steps:** Open http://localhost:8080/products/1, click in **Quantity**, press **Tab**: focus moves to **Add to cart** (press Enter and it adds to cart) but nothing on screen shows it's focused. Links and inputs do show a focus ring.
- **You should see:** a visible focus ring. **What happens:** none.
- **Expected detection:** **keyboard/visual check only** (axe can't detect it). **Verified by:** `test_a10_no_focus_indicator_on_buttons`

---

## Self-healing demo (UI variant)

`POST /admin/ui-variant {"variant": "v2"}` (or env `UI_VARIANT=v2`; compose variable `DEMO_SHOP_UI_VARIANT`)
renames these buttons and labels **and changes their element ids**. `POST /reset` switches back to the default.
Behaviour and all planted defects are unchanged (`verify_bugs.py` forces v1).

| Element | v1 text / id | v2 text / id |
|---|---|---|
| Header login link | Log in / `nav-login` | Sign in / `nav-sign-in` |
| Product page button | Add to cart / `add-to-cart` | Add to bag / `add-to-bag` |
| Cart page button | Checkout / `checkout-button` | Proceed to checkout / `proceed-to-checkout` |
| Checkout submit | Place order / `place-order` | Complete purchase / `complete-purchase` |
| Login email label | Email / `login-email` | Email address / `login-email-address` |
| Login submit | Log in / `login-submit` | Sign in / `sign-in-submit` |
| Signup submit | Create account / `signup-submit` | Register / `register-submit` |
| Contact submit | Send message / `contact-submit` | Submit / `contact-send` |

Demo: record a passing test on v1 → switch to v2 → replay fails on the old locator → Healer finds the renamed
element (same role and position, `data-action` unchanged) → repairs the step and re-runs.
