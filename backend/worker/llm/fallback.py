"""Switch to the next LLM provider when one runs out of tokens.

LLM_PROVIDER is tried first, then LLM_FALLBACKS in order (e.g. LLM_PROVIDER=groq, LLM_FALLBACKS=gemini,ollama).
A provider whose daily quota is used up (LLMQuotaError), or that is still rate-limited after its own retries
(LLMRateLimitError), is skipped and the same request goes to the next one, so the run carries on. Exhausted
providers are remembered for the whole worker process (all runs) and tried again after a cool-down.
"""

import time
from collections.abc import Awaitable, Callable
from typing import ClassVar

from pydantic import BaseModel

from worker.llm.base import LLMProvider, LLMQuotaError, LLMRateLimitError, T, Usage

QUOTA_COOLDOWN_SECONDS = 3600  # daily quota used up: try that provider again in an hour (Groq's limit is a rolling 24 h)
RATE_LIMIT_COOLDOWN_SECONDS = 120  # still rate-limited after retries: give it a couple of minutes

OnSwitch = Callable[[str], Awaitable[None]]


class FallbackProvider(LLMProvider):
    """Looks like one provider to the agents; usage adds up over every provider that answered."""

    # "name/model" -> time.monotonic() until which it's skipped. Shared by all runs in this worker process.
    _skip_until: ClassVar[dict[str, float]] = {}

    def __init__(self, providers: list[LLMProvider], *, on_switch: OnSwitch | None = None):
        if not providers:
            raise ValueError("FallbackProvider needs at least one provider")
        # No super().__init__(): this wraps providers instead of talking to an API itself.
        self.providers = providers
        self.on_switch = on_switch
        self._current: LLMProvider | None = None

    # ---------- what the agents see ----------

    @property
    def name(self) -> str:  # type: ignore[override]
        return (self._current or self.providers[0]).name

    @property
    def model(self) -> str:  # type: ignore[override]
        return (self._current or self.providers[0]).model

    @property
    def usage(self) -> Usage:  # type: ignore[override]
        total = Usage()
        for p in self.providers:
            total.calls += p.usage.calls
            total.input_tokens += p.usage.input_tokens
            total.output_tokens += p.usage.output_tokens
        return total

    @property
    def used(self) -> list[str]:
        """Providers that answered at least one request, e.g. ["groq", "gemini"]."""
        return [p.name for p in self.providers if p.usage.calls]

    async def generate_json(
        self, prompt: str, schema: type[T], images: list[bytes] | None = None, *, schema_hint: str | None = None
    ) -> T:
        problems: list[str] = []
        last: Exception | None = None
        for provider in self.providers:
            if self._skipped(provider):
                problems.append(f"{provider.name} is out of quota (skipped for now)")
                continue
            previous = self._current
            if previous is not None and provider is not previous:
                if self.providers.index(provider) < self.providers.index(previous):
                    await self._announce(f"LLM switched back to {provider.name} (its quota should be available again).")
                else:
                    await self._announce(f"LLM switched from {previous.name} to {provider.name} ({problems[-1]}).")
            self._current = provider
            try:
                return await provider.generate_json(prompt, schema, images, schema_hint=schema_hint)
            except LLMQuotaError as exc:
                self._skip(provider, QUOTA_COOLDOWN_SECONDS)
                problems.append(f"{provider.name}'s daily quota is used up")
                last = exc
            except LLMRateLimitError as exc:
                self._skip(provider, RATE_LIMIT_COOLDOWN_SECONDS)
                problems.append(f"{provider.name} is rate-limited")
                last = exc
        raise LLMQuotaError(
            "Every configured LLM is out of quota right now (" + "; ".join(problems) + "). "
            "Add another provider to LLM_FALLBACKS or try again later."
        ) from last

    async def _complete(self, prompt: str, schema: type[BaseModel], images: list[bytes]) -> str:
        raise NotImplementedError("FallbackProvider delegates generate_json to its providers")

    async def aclose(self) -> None:
        for p in self.providers:
            await p.aclose()

    # ---------- bookkeeping ----------

    @staticmethod
    def _key(provider: LLMProvider) -> str:
        return f"{provider.name}/{provider.model}"

    def _skipped(self, provider: LLMProvider) -> bool:
        until = self._skip_until.get(self._key(provider))
        if until is None:
            return False
        if time.monotonic() >= until:
            del self._skip_until[self._key(provider)]
            return False
        return True

    def _skip(self, provider: LLMProvider, seconds: float) -> None:
        self._skip_until[self._key(provider)] = time.monotonic() + seconds

    async def _announce(self, message: str) -> None:
        if self.on_switch is not None:
            await self.on_switch(message)
