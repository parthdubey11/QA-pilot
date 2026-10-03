"""Button/label text and element ids. Switching the variant renames them (used to demo self-healing)."""

from typing import Literal

Variant = Literal["v1", "v2"]

# key -> (visible text, element id)
LABELS: dict[str, dict[str, tuple[str, str]]] = {
    "v1": {
        "nav_login": ("Log in", "nav-login"),
        "add_to_cart": ("Add to cart", "add-to-cart"),
        "checkout": ("Checkout", "checkout-button"),
        "place_order": ("Place order", "place-order"),
        "login_email": ("Email", "login-email"),
        "login_submit": ("Log in", "login-submit"),
        "signup_submit": ("Create account", "signup-submit"),
        "contact_submit": ("Send message", "contact-submit"),
    },
    "v2": {
        "nav_login": ("Sign in", "nav-sign-in"),
        "add_to_cart": ("Add to bag", "add-to-bag"),
        "checkout": ("Proceed to checkout", "proceed-to-checkout"),
        "place_order": ("Complete purchase", "complete-purchase"),
        "login_email": ("Email address", "login-email-address"),
        "login_submit": ("Sign in", "sign-in-submit"),
        "signup_submit": ("Register", "register-submit"),
        "contact_submit": ("Submit", "contact-send"),
    },
}
