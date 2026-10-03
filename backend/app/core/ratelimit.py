"""Failed-login limiter (brute-force guard). In memory: enough for the single API process QA Pilot runs."""

import time
from collections import defaultdict, deque

MAX_FAILURES = 10
WINDOW_SECONDS = 15 * 60


class LoginLimiter:
    def __init__(self, max_failures: int = MAX_FAILURES, window: float = WINDOW_SECONDS) -> None:
        self.max_failures, self.window = max_failures, window
        self._failures: dict[str, deque[float]] = defaultdict(deque)

    def _recent(self, key: str) -> deque[float]:
        q, cutoff = self._failures[key], time.monotonic() - self.window
        while q and q[0] < cutoff:
            q.popleft()
        return q

    def retry_after(self, key: str) -> int | None:
        """Seconds until `key` may try again, or None if it isn't locked out."""
        q = self._recent(key)
        if len(q) < self.max_failures:
            return None
        return max(1, int(q[0] + self.window - time.monotonic()))

    def failed(self, key: str) -> None:
        self._recent(key).append(time.monotonic())

    def succeeded(self, key: str) -> None:
        self._failures.pop(key, None)


login_limiter = LoginLimiter()
