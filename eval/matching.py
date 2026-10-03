"""Match QA Pilot findings against demo-shop/GROUND_TRUTH.md.

Functional bugs: each planted bug has keyword groups; a reported bug matches when its text (title, expected,
actual, steps) contains at least one term of EVERY group. Deterministic, so results are reproducible and cost
no extra LLM calls. Accessibility: each planted violation is a rule id (axe or keyboard check) on a page.
"""

import re
from dataclasses import dataclass

# id -> (short name, keyword groups: all groups must match, any term within a group)
FUNCTIONAL: dict[str, tuple[str, list[list[str]]]] = {
    "F01": ("Cart accepts negative quantity", [
        ["negative", "-1", "-2", "-3", "-5", "below zero", "less than 1", "less than one", "minus"], ["quantit"]]),
    "F02": ("Duplicate email signup allowed", [
        ["duplicate", "already registered", "already exists", "already in use", "same email", "twice",
         "second account", "multiple accounts", "existing email", "already been registered"],
        ["email", "sign up", "signup", "sign-up", "account", "regist"]]),
    "F03": ("Total not updated after removing an item", [
        ["remov", "delet", "trash"], ["total"],
        ["not update", "doesn't update", "does not update", "didn't update", "unchanged", "still show", "stale",
         "not recalculat", "remain", "not refresh", "until reload", "until the page is reloaded"]]),
    "F04": ("Contact form accepts invalid email", [
        ["contact"], ["email"], ["invalid", "malformed", "not-an-email", "without @", "format", "any email"]]),
    "F05": ("Logout doesn't clear the session", [
        ["log out", "logout", "logging out", "sign out", "signing out", "logged out"],
        ["still", "remain", "not clear", "persist", "session", "stays logged", "not logged out"]]),
    "F06": ("Sale price not applied in cart", [
        ["sale", "discount", "49.99", "39.99", "full price", "original price", "regular price"],
        ["cart", "checkout", "charged", "total"]]),
    "F07": ("Checkout allowed with an empty cart", [
        ["empty cart", "cart is empty", "cart was empty", "no items", "empty"], ["checkout", "order", "place order"]]),
    "F08": ("Password shorter than the stated minimum accepted", [
        ["password"], ["short", "8 characters", "eight characters", "minimum", "length", "7-character",
                       "fewer than", "less than 8", "too short", "characters long"]]),
    "F09": ("Out-of-stock product can be added", [
        ["out of stock", "out-of-stock", "stock of 0", "no stock", "zero stock", "unavailable item"], ["cart", "add"]]),
    "F10": ("Header cart count not updated after adding", [
        ["badge", "cart count", "counter", "header", "cart icon", "item count", "cart indicator"],
        ["not update", "doesn't update", "does not update", "didn't update", "stale", "remain", "still show",
         "until reload", "until the page is reloaded", "refresh", "stays at"]]),
    "F11": ("Non-numeric product URL shows raw JSON", [
        ["json", "422", "int_parsing", "raw error", "raw api", "unformatted"], ["product", "url", "/products/"]]),
    "F12": ("Login from checkout doesn't return to checkout", [
        ["redirect", "return", "back to", "lands on", "landed on", "home page", "homepage", "sent to", "taken to"],
        ["checkout"], ["login", "log in", "logging in", "sign in"]]),
    "F13": ("Mixed-case email can't log in after signup", [
        ["case", "uppercase", "upper-case", "capital", "mixed"], ["email"], ["log in", "login", "sign in", "logging in"]]),
    "F14": ("Can order more than the available stock", [
        ["stock", "available", "inventory", "left"], ["more than", "exceed", "above", "greater than", "beyond"]]),
    "F15": ('"Price: low to high" sort is wrong', [
        ["sort", "order"], ["low to high", "ascending", "price"],
        ["wrong", "incorrect", "not sorted", "out of order", "before", "alphabetical", "lexicograph", "string"]]),
}

# id -> (short name, rule id, page path regex or None = any page)
ACCESSIBILITY: dict[str, tuple[str, str, str | None]] = {
    "A01": ("Missing lang on <html>", "html-has-lang", r"^/contact"),
    "A02": ("Images without alt", "image-alt", r"^/$"),
    "A03": ("Form field without label", "label", r"^/signup"),
    "A04": ("Icon-only button without name", "button-name", r"^/cart"),
    "A05": ("Low text contrast", "color-contrast", r"^/products/"),
    "A06": ("Keyboard trap", "keyboard-trap", r"^/checkout"),
    "A07": ("Icon-only link without name", "link-name", None),
    "A08": ("Missing page <title>", "document-title", r"^/login"),
    "A09": ("Zoom disabled", "meta-viewport", r"^/products/"),
    "A10": ("No visible focus indicator", "focus-not-visible", None),
}


def bug_text(bug: dict) -> str:
    parts = [bug.get("title", ""), bug.get("expected", ""), bug.get("actual", ""), *bug.get("steps", [])]
    return " ".join(parts).lower()


def match_bug(bug: dict) -> str | None:
    """The planted bug this report describes, or None (a false positive). If several match, the one with more
    keyword groups (the more specific description) wins."""
    text = bug_text(bug)
    matches = [
        (len(groups), gt_id)
        for gt_id, (_, groups) in FUNCTIONAL.items()
        if all(any(term in text for term in group) for group in groups)
    ]
    if not matches:
        return None
    best = max(n for n, _ in matches)
    return next(gt_id for n, gt_id in matches if n == best)


def match_a11y(rule_id: str, page_path: str) -> str | None:
    path = page_path.split("?")[0] or "/"
    for gt_id, (_, rule, page) in ACCESSIBILITY.items():
        if rule == rule_id and (page is None or re.search(page, path)):
            return gt_id
    return None


@dataclass
class Score:
    found: set[str]  # distinct ground-truth ids found
    true_positives: int  # reported findings that match a ground-truth item
    reported: int  # all reported findings
    total: int  # ground-truth items

    @property
    def recall(self) -> float:
        return len(self.found) / self.total if self.total else 0.0

    @property
    def precision(self) -> float | None:
        return self.true_positives / self.reported if self.reported else None


def score_bugs(bugs: list[dict]) -> tuple[Score, list[dict]]:
    """Score reported bugs; returns the score and each bug annotated with its match."""
    annotated = [bug | {"match": match_bug(bug)} for bug in bugs]
    found = {b["match"] for b in annotated if b["match"]}
    tp = sum(1 for b in annotated if b["match"])
    return Score(found=found, true_positives=tp, reported=len(bugs), total=len(FUNCTIONAL)), annotated


def score_a11y(findings: list[tuple[str, str]]) -> tuple[Score, list[tuple[str, str, str | None]]]:
    """findings: (rule_id, page_path) pairs."""
    annotated = [(rule, path, match_a11y(rule, path)) for rule, path in findings]
    found = {m for _, _, m in annotated if m}
    tp = sum(1 for _, _, m in annotated if m)
    return Score(found=found, true_positives=tp, reported=len(findings), total=len(ACCESSIBILITY)), annotated
