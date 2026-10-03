"""In-memory shop data. `state.reset()` restores the seed data (used by POST /reset)."""

import hashlib
import secrets
from dataclasses import dataclass, field


@dataclass
class Product:
    id: int
    name: str
    description: str
    price: int  # cents
    sale_price: int | None
    stock: int
    image: str


@dataclass
class User:
    id: int
    name: str
    email: str
    salt: str
    password_hash: str


@dataclass
class Order:
    id: int
    user_id: int
    items: list[dict[str, int]]
    total: int
    full_name: str
    address: str
    city: str
    country: str


@dataclass
class ContactMessage:
    name: str
    email: str
    message: str


def hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 50_000).hex()


def make_user(user_id: int, name: str, email: str, password: str) -> User:
    salt = secrets.token_hex(8)
    return User(user_id, name, email, salt, hash_password(password, salt))


SEED_PRODUCTS = [
    Product(1, "Trail Runner Shoes", "Lightweight running shoes with a grippy sole for muddy trails.",
            8999, None, 12, "shoes.svg"),
    Product(2, "Canvas Backpack", "A 20-litre everyday backpack with a padded laptop sleeve.",
            4999, 3999, 8, "backpack.svg"),
    Product(3, "Steel Water Bottle", "Keeps drinks cold for 24 hours. 750 ml, dishwasher safe.",
            1999, None, 30, "bottle.svg"),
    Product(4, "Wool Beanie", "Warm merino wool beanie, one size fits most.",
            2499, None, 0, "beanie.svg"),
    Product(5, "Rain Jacket", "Packable waterproof jacket with taped seams and a hood.",
            12999, 10999, 5, "jacket.svg"),
    Product(6, "Travel Mug", "Leak-proof 350 ml mug that fits most car cup holders.",
            1499, None, 20, "mug.svg"),
]


@dataclass
class Store:
    products: dict[int, Product] = field(default_factory=dict)
    users: list[User] = field(default_factory=list)
    sessions: dict[str, int] = field(default_factory=dict)
    carts: dict[str, dict[int, int]] = field(default_factory=dict)
    orders: list[Order] = field(default_factory=list)
    messages: list[ContactMessage] = field(default_factory=list)
    next_user_id: int = 1
    next_order_id: int = 1001

    def reset(self) -> None:
        self.products = {p.id: Product(**vars(p)) for p in SEED_PRODUCTS}
        self.users = [make_user(1, "Demo User", "demo@shop.test", "demo1234")]
        self.sessions = {}
        self.carts = {}
        self.orders = []
        self.messages = []
        self.next_user_id = 2
        self.next_order_id = 1001

    def add_user(self, name: str, email: str, password: str) -> User:
        user = make_user(self.next_user_id, name, email, password)
        self.next_user_id += 1
        self.users.append(user)
        return user


state = Store()
state.reset()
