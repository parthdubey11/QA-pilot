"""Safeguards for running the demo shop on the public internet.

The shop is buggy on purpose (logic and accessibility bugs only, see GROUND_TRUTH.md). None of these limits change
a planted bug; they only stop one visitor from flooding a small free server or the shop being mistaken for a real one.

Settings (environment variables):
- RESET_TOKEN: if set, POST /reset and the UI-variant switch need the header `X-Reset-Token: <token>`.
- RATE_LIMIT_PER_MINUTE: requests per client IP per minute (default 600; 0 disables).
- TRUST_PROXY: "1" when behind a reverse proxy (Caddy) so the client IP comes from X-Forwarded-For.
- NIGHTLY_RESET_HOUR_UTC: hour (0-23) for the automatic daily reset (default 3; "off" disables).
"""

import asyncio
import hmac
import logging
import os
import time
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse

log = logging.getLogger("demo_shop")

# Size caps (reset nightly). Generous enough for testing, small enough for a free server.
MAX_USERS = 500
MAX_SESSIONS = 2000
MAX_CARTS = 5000
MAX_CART_LINES = 20
MAX_QUANTITY = 999  # absolute value: negative quantities stay possible (planted bug F01), and so does ordering above stock (F14)
MAX_ORDERS = 1000
MAX_MESSAGES = 200
MAX_TEXT = 500  # characters per text field

BANNER = (
    '<p class="demo-banner" role="note">Intentionally buggy demo site for testing QA Pilot. '
    "Don't enter real information.</p>"
)
NOINDEX_META = '<meta name="robots" content="noindex, nofollow">'
ROBOTS_TXT = "User-agent: *\nDisallow: /\n"


# ---------- reset token ----------

def check_reset_token(request: Request) -> None:
    expected = os.getenv("RESET_TOKEN", "")
    if not expected:
        return  # local development: open
    given = request.headers.get("x-reset-token", "")
    if not hmac.compare_digest(given.encode(), expected.encode()):
        raise HTTPException(403, "Reset needs the X-Reset-Token header.")


# ---------- rate limiting ----------

class RateLimiter:
    """Sliding one-minute window per client IP, in memory (single process)."""

    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self.hits: "OrderedDict[str, deque[float]]" = OrderedDict()

    def allow(self, key: str) -> bool:
        if self.per_minute <= 0:
            return True
        now = time.monotonic()
        q = self.hits.pop(key, None) or deque()
        while q and q[0] < now - 60:
            q.popleft()
        self.hits[key] = q  # most recently seen last
        while len(self.hits) > 10_000:  # forget the least recently seen clients
            self.hits.popitem(last=False)
        if len(q) >= self.per_minute:
            return False
        q.append(now)
        return True


limiter = RateLimiter(int(os.getenv("RATE_LIMIT_PER_MINUTE", "600")))


def client_ip(request: Request) -> str:
    if os.getenv("TRUST_PROXY") == "1":
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def safety_middleware(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    if not limiter.allow(client_ip(request)):
        return JSONResponse({"detail": "Too many requests. Please slow down."}, status_code=429, headers={"Retry-After": "60"})
    response = await call_next(request)
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


def robots_txt() -> PlainTextResponse:
    return PlainTextResponse(ROBOTS_TXT)


# ---------- size caps ----------

def trim_oldest(mapping: dict, limit: int) -> None:
    """Drop the oldest entries (dicts keep insertion order) until `mapping` has room for one more."""
    while len(mapping) >= limit:
        mapping.pop(next(iter(mapping)))


def check_text(*values: str) -> None:
    if any(len(v) > MAX_TEXT for v in values):
        raise HTTPException(400, f"Please keep each field under {MAX_TEXT} characters.")


def check_quantity(quantity: int) -> None:
    if abs(quantity) > MAX_QUANTITY:
        raise HTTPException(400, f"Quantity can't be more than {MAX_QUANTITY}.")


# ---------- nightly reset ----------

def seconds_until(hour_utc: int, now: datetime | None = None) -> float:
    now = now or datetime.now(timezone.utc)
    target = now.replace(hour=hour_utc, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def nightly_reset(reset: Callable[[], None]) -> None:
    setting = os.getenv("NIGHTLY_RESET_HOUR_UTC", "3")
    if setting.lower() == "off":
        return
    hour = int(setting) % 24
    while True:
        await asyncio.sleep(seconds_until(hour))
        reset()
        log.info("nightly reset done")
