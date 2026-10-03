"""Demo Shop: a small, deliberately buggy web shop used as QA Pilot's test target.

The planted bugs are documented in GROUND_TRUTH.md. Do not fix them here.
"""

import asyncio
import os
import re
import secrets
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import safety
from app.labels import LABELS, Variant
from app.store import ContactMessage, Order, Product, User, hash_password, state

BASE_DIR = Path(__file__).parent
PAGES_DIR = BASE_DIR / "pages"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
CARD_RE = re.compile(r"^\d{16}$")

DEFAULT_VARIANT: Variant = "v2" if os.getenv("UI_VARIANT") == "v2" else "v1"
ui: dict[str, Variant] = {"variant": DEFAULT_VARIANT}



def reset_shop() -> None:
    state.reset()
    ui["variant"] = DEFAULT_VARIANT


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    task = asyncio.create_task(safety.nightly_reset(reset_shop))
    yield
    task.cancel()


app = FastAPI(title="Demo Shop", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.middleware("http")(safety.safety_middleware)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
app.add_api_route("/robots.txt", safety.robots_txt, methods=["GET"], include_in_schema=False)


# ---------- pages ----------

_PLACEHOLDER = re.compile(r"\{\{(label|id):(\w+)\}\}")


def render_page(filename: str) -> HTMLResponse:
    html = (PAGES_DIR / filename).read_text(encoding="utf-8")
    html = html.replace("{{header}}", (PAGES_DIR / "_header.html").read_text(encoding="utf-8"))
    html = html.replace("{{banner}}", safety.BANNER)
    html = html.replace('<meta charset="utf-8">', '<meta charset="utf-8">\n  ' + safety.NOINDEX_META, 1)
    html = html.replace("{{footer}}", (PAGES_DIR / "_footer.html").read_text(encoding="utf-8"))
    labels = LABELS[ui["variant"]]
    html = _PLACEHOLDER.sub(lambda m: labels[m.group(2)][0 if m.group(1) == "label" else 1], html)
    return HTMLResponse(html)


def _page_route(filename: str) -> Callable[[], HTMLResponse]:
    def handler() -> HTMLResponse:
        return render_page(filename)

    return handler


for _path, _file in {
    "/": "index.html",
    "/cart": "cart.html",
    "/checkout": "checkout.html",
    "/signup": "signup.html",
    "/login": "login.html",
    "/contact": "contact.html",
}.items():
    app.add_api_route(_path, _page_route(_file), methods=["GET"], response_class=HTMLResponse)


@app.get("/products/{product_id}", response_class=HTMLResponse)
def product_page(product_id: int) -> HTMLResponse:
    return render_page("product.html")


# ---------- helpers ----------


def current_user(request: Request) -> User | None:
    user_id = state.sessions.get(request.cookies.get("sid", ""))
    return next((u for u in state.users if u.id == user_id), None)


def get_cart(request: Request, response: Response) -> dict[int, int]:
    cart_id = request.cookies.get("cart_id")
    if not cart_id or cart_id not in state.carts:
        cart_id = cart_id or secrets.token_urlsafe(16)
        safety.trim_oldest(state.carts, safety.MAX_CARTS)
        response.set_cookie("cart_id", cart_id, httponly=True, samesite="lax")
    return state.carts.setdefault(cart_id, {})


def cart_summary(cart: dict[int, int]) -> dict:
    items = []
    for product_id, quantity in cart.items():
        product = state.products[product_id]
        unit_price = product.price
        items.append({
            "product_id": product_id,
            "name": product.name,
            "unit_price": unit_price,
            "quantity": quantity,
            "line_total": unit_price * quantity,
        })
    return {
        "items": items,
        "total": sum(i["line_total"] for i in items),
        "count": sum(i["quantity"] for i in items),
    }


def product_json(product: Product) -> dict:
    return vars(product) | {"image": f"/static/img/{product.image}"}


# ---------- products & cart ----------


@app.get("/api/products")
def list_products() -> list[dict]:
    return [product_json(p) for p in state.products.values()]


@app.get("/api/products/{product_id}")
def get_product(product_id: int) -> dict:
    product = state.products.get(product_id)
    if product is None:
        raise HTTPException(404, "Product not found")
    return product_json(product)


class CartAdd(BaseModel):
    product_id: int
    quantity: int = 1


class CartUpdate(BaseModel):
    quantity: int


@app.get("/api/cart")
def read_cart(cart: dict[int, int] = Depends(get_cart)) -> dict:
    return cart_summary(cart)


@app.post("/api/cart/items")
def add_to_cart(body: CartAdd, cart: dict[int, int] = Depends(get_cart)) -> dict:
    if body.product_id not in state.products:
        raise HTTPException(404, "Product not found")
    if body.product_id not in cart and len(cart) >= safety.MAX_CART_LINES:
        raise HTTPException(400, f"A cart can hold at most {safety.MAX_CART_LINES} different products.")
    safety.check_quantity(cart.get(body.product_id, 0) + body.quantity)
    cart[body.product_id] = cart.get(body.product_id, 0) + body.quantity
    return cart_summary(cart)


@app.patch("/api/cart/items/{product_id}")
def update_cart_item(product_id: int, body: CartUpdate, cart: dict[int, int] = Depends(get_cart)) -> dict:
    if product_id not in cart:
        raise HTTPException(404, "Item not in cart")
    safety.check_quantity(body.quantity)
    cart[product_id] = body.quantity
    return cart_summary(cart)


@app.delete("/api/cart/items/{product_id}")
def remove_cart_item(product_id: int, cart: dict[int, int] = Depends(get_cart)) -> dict:
    cart.pop(product_id, None)
    return cart_summary(cart)


# ---------- accounts ----------


class SignupBody(BaseModel):
    name: str
    email: str
    password: str


class LoginBody(BaseModel):
    email: str
    password: str


@app.post("/api/signup", status_code=201)
def signup(body: SignupBody) -> dict:
    if not body.name.strip():
        raise HTTPException(400, "Please enter your name.")
    if not EMAIL_RE.match(body.email.strip()):
        raise HTTPException(400, "Please enter a valid email address.")
    if not body.password:
        raise HTTPException(400, "Please choose a password.")
    safety.check_text(body.name, body.email, body.password)
    if len(state.users) >= safety.MAX_USERS:
        raise HTTPException(503, "The demo shop is full for today. It resets every night.")
    user = state.add_user(body.name.strip(), body.email.strip(), body.password)
    return {"id": user.id, "name": user.name, "email": user.email}


@app.post("/api/login")
def login(body: LoginBody, response: Response) -> dict:
    email = body.email.strip().lower()
    for user in state.users:
        if user.email == email and hash_password(body.password, user.salt) == user.password_hash:
            token = secrets.token_urlsafe(24)
            safety.trim_oldest(state.sessions, safety.MAX_SESSIONS)
            state.sessions[token] = user.id
            response.set_cookie("sid", token, httponly=True, samesite="lax", path="/")
            return {"name": user.name, "email": user.email}
    raise HTTPException(401, "Invalid email or password.")


@app.post("/api/logout")
def logout(response: Response) -> dict:
    response.delete_cookie("sid", path="/api")
    return {"ok": True}


@app.get("/api/me")
def me(request: Request) -> dict:
    user = current_user(request)
    return {"user": {"name": user.name, "email": user.email} if user else None}


# ---------- checkout & contact ----------


class CheckoutBody(BaseModel):
    full_name: str
    address: str
    city: str
    country: str
    card_number: str
    promo_code: str = ""


@app.post("/api/checkout", status_code=201)
def checkout(body: CheckoutBody, request: Request, cart: dict[int, int] = Depends(get_cart)) -> dict:
    user = current_user(request)
    if user is None:
        raise HTTPException(401, "Please log in to check out.")
    if not all(v.strip() for v in (body.full_name, body.address, body.city, body.country)):
        raise HTTPException(400, "Please fill in your name and full address.")
    if not CARD_RE.match(body.card_number.replace(" ", "")):
        raise HTTPException(400, "Card number must be 16 digits.")
    safety.check_text(body.full_name, body.address, body.city, body.country, body.promo_code)
    if len(state.orders) >= safety.MAX_ORDERS:
        state.orders.pop(0)
    summary = cart_summary(cart)
    order = Order(
        id=state.next_order_id,
        user_id=user.id,
        items=[{"product_id": i["product_id"], "quantity": i["quantity"]} for i in summary["items"]],
        total=summary["total"],
        full_name=body.full_name,
        address=body.address,
        city=body.city,
        country=body.country,
    )
    state.next_order_id += 1
    state.orders.append(order)
    for item in order.items:
        product = state.products[item["product_id"]]
        product.stock = max(0, product.stock - item["quantity"])
    cart.clear()
    return {"order_id": order.id, "total": order.total}


class ContactBody(BaseModel):
    name: str
    email: str
    message: str


@app.post("/api/contact", status_code=201)
def contact(body: ContactBody) -> dict:
    if not body.name.strip() or not body.message.strip():
        raise HTTPException(400, "Please fill in your name and a message.")
    if not body.email.strip():
        raise HTTPException(400, "Please enter your email address.")
    safety.check_text(body.name, body.email)
    if len(body.message) > 5000:
        raise HTTPException(400, "Please keep your message under 5000 characters.")
    if len(state.messages) >= safety.MAX_MESSAGES:
        state.messages.pop(0)
    state.messages.append(ContactMessage(body.name, body.email, body.message))
    return {"ok": True}


# ---------- test-harness controls (used by QA Pilot's eval, not part of the "shop") ----------


class VariantBody(BaseModel):
    variant: Variant


@app.post("/reset")
def reset(request: Request) -> dict:
    """Restore seed data and the default UI variant. Needs X-Reset-Token when RESET_TOKEN is set."""
    safety.check_reset_token(request)
    reset_shop()
    return {"ok": True, "variant": ui["variant"]}


@app.get("/admin/ui-variant")
def get_ui_variant() -> dict:
    return {"variant": ui["variant"]}


@app.post("/admin/ui-variant")
def set_ui_variant(body: VariantBody, request: Request) -> dict:
    """v1 = original labels; v2 = renamed buttons, labels and ids (demo of self-healing)."""
    safety.check_reset_token(request)
    ui["variant"] = body.variant
    return {"variant": ui["variant"]}
