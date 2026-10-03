"""Provider-agnostic LLM interface: generate_json(prompt, schema, images) -> validated Pydantic object.

If the model's reply isn't valid JSON for the schema, we retry once with the validation error; a second
bad reply raises LLMOutputError so the caller can fail the step gracefully.
"""

import asyncio
import json
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from worker.agents import load_prompt


class LLMError(Exception):
    """The provider couldn't be reached or returned an error."""


class LLMQuotaError(LLMError):
    """The provider's daily quota is exhausted: retrying today won't help."""


class LLMRateLimitError(LLMError):
    """Still HTTP 429 after all retries (per-minute limits): another provider may be able to answer."""


class LLMOutputError(LLMError):
    """The model's reply wasn't valid JSON for the schema, even after one retry."""

    def __init__(self, message: str, raw: str):
        super().__init__(message)
        self.raw = raw


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


T = TypeVar("T", bound=BaseModel)

_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


def parse_json_reply(raw: str, schema: type[T]) -> T:
    """Validate a model reply against the schema, tolerating a Markdown code fence around it."""
    match = _FENCE.match(raw)
    return schema.model_validate_json(match.group(1) if match else raw.strip())


def image_mime(data: bytes) -> str:
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


class LLMProvider(ABC):
    name: str = "base"

    def __init__(
        self,
        model: str,
        *,
        http: httpx.AsyncClient | None = None,
        timeout: float = 90.0,
        min_interval: float = 0.0,
        max_retries: int = 3,
    ):
        self.model = model
        self.http = http or httpx.AsyncClient(timeout=timeout)
        self.usage = Usage()
        self.min_interval = min_interval  # seconds between requests (free-tier rate limits)
        self.max_retries = max_retries  # retries on 429 / 5xx
        self._last_request = 0.0
        self._lock = asyncio.Lock()

    async def generate_json(
        self, prompt: str, schema: type[T], images: list[bytes] | None = None, *, schema_hint: str | None = None
    ) -> T:
        """schema_hint: a short description of the expected JSON, sent instead of the full JSON Schema when the
        prompt already documents the fields (saves tokens). The reply is always validated against `schema`."""
        schema_text = schema_hint or json.dumps(schema.model_json_schema(), indent=None)
        full_prompt = f"{prompt}\n\n{load_prompt('json_format').format(schema=schema_text)}"
        raw = await self._complete(full_prompt, schema, images or [])
        try:
            return parse_json_reply(raw, schema)
        except (ValidationError, ValueError) as first_error:
            retry_prompt = f"{full_prompt}\n\n" + load_prompt("json_retry").format(
                error=_short_error(first_error), previous=raw[:4000]
            )
            raw = await self._complete(retry_prompt, schema, images or [])
            try:
                return parse_json_reply(raw, schema)
            except (ValidationError, ValueError) as second_error:
                raise LLMOutputError(
                    f"{self.name} returned invalid JSON twice: {_short_error(second_error)}", raw
                ) from second_error

    @abstractmethod
    async def _complete(self, prompt: str, schema: type[BaseModel], images: list[bytes]) -> str:
        """Send one request and return the model's text reply (and update self.usage)."""

    async def _post(self, url: str, *, json_body: dict, headers: dict[str, str] | None = None) -> dict:
        for attempt in range(self.max_retries + 1):
            async with self._lock:  # one request at a time, spaced by min_interval
                wait = self._last_request + self.min_interval - time.monotonic()
                if wait > 0:
                    await asyncio.sleep(wait)
                try:
                    response = await self.http.post(url, json=json_body, headers=headers or {})
                except httpx.HTTPError as exc:
                    raise LLMError(f"{self.name}: request failed ({type(exc).__name__})") from exc
                finally:
                    self._last_request = time.monotonic()
            if is_daily_quota(response):
                raise LLMQuotaError(
                    f"{self.name}: the daily request quota for {self.model} is used up ({provider_message(response)}). "
                    "Try again tomorrow, set another model (e.g. GEMINI_MODEL) or provider (LLM_PROVIDER), "
                    "or enable billing."
                )
            if response.status_code in RETRY_STATUSES and attempt < self.max_retries:
                await asyncio.sleep(retry_delay(response, attempt))
                continue
            break
        if response.status_code == 429:
            raise LLMRateLimitError(f"{self.name}: still rate-limited after {self.max_retries} retries "
                                    f"({provider_message(response)})")
        if response.status_code >= 400:
            # Don't echo headers or the request: they may contain the API key.
            raise LLMError(f"{self.name}: HTTP {response.status_code}: {provider_message(response)}")
        try:
            return response.json()
        except ValueError as exc:
            raise LLMError(f"{self.name}: response was not JSON") from exc

    async def aclose(self) -> None:
        await self.http.aclose()


RETRY_STATUSES = {429, 500, 502, 503, 504}
_RETRY_DELAY = re.compile(r'"retryDelay":\s*"(\d+(?:\.\d+)?)s"')


def provider_message(response: httpx.Response) -> str:
    """The human-readable error from a provider response (first line, trimmed), not the raw JSON."""
    try:
        data = response.json()
    except ValueError:
        data = None
    message = ""
    if isinstance(data, dict):
        error = data.get("error")
        message = (error.get("message") if isinstance(error, dict) else error) or data.get("message") or ""
    message = str(message or response.text or f"HTTP {response.status_code}").strip().splitlines()[0]
    return message[:300]


def is_daily_quota(response: httpx.Response) -> bool:
    """A 429 for a per-day quota — unlike per-minute limits, waiting a few seconds won't fix it.
    Gemini: quotaId "...PerDay..."; Groq: "tokens per day (TPD)" / "requests per day (RPD)"."""
    if response.status_code != 429:
        return False
    text = response.text.lower()
    return "perday" in text or "per day" in text


def retry_delay(response: httpx.Response, attempt: int) -> float:
    """Seconds to wait before retrying: Retry-After header, Gemini's retryDelay, else exponential backoff."""
    header = response.headers.get("retry-after", "")
    if header.replace(".", "", 1).isdigit():
        return min(float(header), 90.0)
    match = _RETRY_DELAY.search(response.text)
    if match:
        return min(float(match.group(1)) + 1, 90.0)
    return min(5.0 * 2**attempt, 60.0)


def _short_error(error: Exception) -> str:
    if isinstance(error, ValidationError):
        return "; ".join(f"{'.'.join(map(str, e['loc'])) or '(root)'}: {e['msg']}" for e in error.errors()[:10])
    return str(error)[:500]
