"""Checks the shop works AND that every planted server-side bug is still present.

If one of the `test_bug_*` tests fails, a planted bug was accidentally fixed; GROUND_TRUTH.md would then be wrong.
Client-side bugs (F03, F10, F12, F15) and accessibility violations are checked in a real browser by eval/, not here.
"""

import re
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.store import state

CARD = "4242424242424242"
ADDRESS = {"full_name": "Demo User", "address": "1 Main St", "city": "Pune", "country": "India", "card_number": CARD}


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        assert c.post("/reset").status_code == 200
        yield c


def login(client: TestClient, email: str = "demo@shop.test", password: str = "demo1234") -> None:
    res = client.post("/api/login", json={"email": email, "password": password})
    assert res.status_code == 200, res.text


# ---------- working features ----------


@pytest.mark.parametrize("path", ["/", "/products/1", "/cart", "/checkout", "/signup", "/login", "/contact"])
def test_pages_render_without_placeholders(client: TestClient, path: str) -> None:
    res = client.get(path)
    assert res.status_code == 200
    assert "{{" not in res.text


def test_products_listed(client: TestClient) -> None:
    products = client.get("/api/products").json()
    assert len(products) == 6
    assert client.get("/api/products/999").status_code == 404


def test_login_rejects_wrong_password(client: TestClient) -> None:
    res = client.post("/api/login", json={"email": "demo@shop.test", "password": "wrong"})
    assert res.status_code == 401


def test_cart_add_update_remove(client: TestClient) -> None:
    client.post("/api/cart/items", json={"product_id": 1, "quantity": 2})
    cart = client.patch("/api/cart/items/1", json={"quantity": 3}).json()
    assert cart["total"] == 3 * 8999 and cart["count"] == 3
    cart = client.delete("/api/cart/items/1").json()
    assert cart == {"items": [], "total": 0, "count": 0}


def test_checkout_requires_login(client: TestClient) -> None:
    client.post("/api/cart/items", json={"product_id": 1, "quantity": 1})
    assert client.post("/api/checkout", json=ADDRESS).status_code == 401


def test_checkout_creates_order_and_clears_cart(client: TestClient) -> None:
    login(client)
    client.post("/api/cart/items", json={"product_id": 3, "quantity": 2})
    res = client.post("/api/checkout", json=ADDRESS)
    assert res.status_code == 201
    assert res.json()["total"] == 2 * 1999
    assert client.get("/api/cart").json()["count"] == 0
    assert state.products[3].stock == 28


def test_checkout_validates_card_and_address(client: TestClient) -> None:
    login(client)
    client.post("/api/cart/items", json={"product_id": 1, "quantity": 1})
    assert client.post("/api/checkout", json=ADDRESS | {"card_number": "1234"}).status_code == 400
    assert client.post("/api/checkout", json=ADDRESS | {"city": ""}).status_code == 400


def test_signup_rejects_invalid_email(client: TestClient) -> None:
    res = client.post("/api/signup", json={"name": "A", "email": "not-an-email", "password": "longenough"})
    assert res.status_code == 400


def test_contact_requires_message(client: TestClient) -> None:
    res = client.post("/api/contact", json={"name": "A", "email": "a@b.co", "message": " "})
    assert res.status_code == 400


def test_reset_restores_seed_data(client: TestClient) -> None:
    client.post("/api/signup", json={"name": "New", "email": "new@x.io", "password": "password1"})
    login(client)
    client.post("/api/cart/items", json={"product_id": 1, "quantity": 5})
    client.post("/api/checkout", json=ADDRESS)
    client.post("/admin/ui-variant", json={"variant": "v2"})

    assert client.post("/reset").json() == {"ok": True, "variant": "v1"}

    assert len(state.users) == 1 and state.orders == [] and state.sessions == {}
    assert state.products[1].stock == 12
    assert client.get("/api/me").json() == {"user": None}


def test_ui_variant_renames_labels_and_ids(client: TestClient) -> None:
    v1 = client.get("/products/1").text
    assert 'id="add-to-cart"' in v1 and ">Add to cart<" in v1

    assert client.post("/admin/ui-variant", json={"variant": "v2"}).json() == {"variant": "v2"}
    v2 = client.get("/products/1").text
    assert 'id="add-to-bag"' in v2 and ">Add to bag<" in v2 and "Add to cart" not in v2
    login_page = client.get("/login").text
    assert ">Sign in<" in login_page and ">Email address<" in login_page

    assert client.post("/admin/ui-variant", json={"variant": "v3"}).status_code == 422


# ---------- planted functional bugs (must stay present) ----------


def test_bug_f01_negative_quantity_accepted(client: TestClient) -> None:
    cart = client.post("/api/cart/items", json={"product_id": 1, "quantity": -2}).json()
    assert cart["total"] == -2 * 8999


def test_bug_f02_duplicate_email_signup_allowed(client: TestClient) -> None:
    body = {"name": "Twin", "email": "demo@shop.test", "password": "password1"}
    assert client.post("/api/signup", json=body).status_code == 201


def test_bug_f04_contact_accepts_invalid_email(client: TestClient) -> None:
    res = client.post("/api/contact", json={"name": "A", "email": "not-an-email", "message": "Hi"})
    assert res.status_code == 201


def test_bug_f05_logout_does_not_end_session(client: TestClient) -> None:
    login(client)
    res = client.post("/api/logout")
    assert re.search(r"sid=.*Path=/api", res.headers["set-cookie"])
    assert client.get("/api/me").json()["user"] is not None


def test_bug_f06_cart_charges_full_price_for_sale_item(client: TestClient) -> None:
    product = client.get("/api/products/2").json()
    assert product["sale_price"] == 3999
    cart = client.post("/api/cart/items", json={"product_id": 2, "quantity": 1}).json()
    assert cart["total"] == 4999


def test_bug_f07_checkout_with_empty_cart_succeeds(client: TestClient) -> None:
    login(client)
    res = client.post("/api/checkout", json=ADDRESS)
    assert res.status_code == 201 and res.json()["total"] == 0


def test_bug_f08_short_password_accepted(client: TestClient) -> None:
    res = client.post("/api/signup", json={"name": "A", "email": "short@pw.io", "password": "abc"})
    assert res.status_code == 201


def test_bug_f09_out_of_stock_product_can_be_added(client: TestClient) -> None:
    assert client.get("/api/products/4").json()["stock"] == 0
    cart = client.post("/api/cart/items", json={"product_id": 4, "quantity": 1}).json()
    assert cart["count"] == 1


def test_bug_f11_non_numeric_product_url_returns_raw_json(client: TestClient) -> None:
    res = client.get("/products/abc")
    assert res.status_code == 422 and res.headers["content-type"] == "application/json"


def test_bug_f13_mixed_case_email_cannot_log_in(client: TestClient) -> None:
    body = {"name": "Case", "email": "Case.User@Shop.test", "password": "password1"}
    assert client.post("/api/signup", json=body).status_code == 201
    res = client.post("/api/login", json={"email": "Case.User@Shop.test", "password": "password1"})
    assert res.status_code == 401


def test_bug_f14_can_order_more_than_stock(client: TestClient) -> None:
    login(client)
    assert state.products[2].stock == 8
    client.post("/api/cart/items", json={"product_id": 2, "quantity": 20})
    res = client.post("/api/checkout", json=ADDRESS)
    assert res.status_code == 201
    assert state.products[2].stock == 0
