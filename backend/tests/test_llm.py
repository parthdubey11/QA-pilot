"""LLM providers with the HTTP layer mocked (no real model is ever called)."""

import base64
import json
from collections.abc import Callable

import httpx
import pytest
from pydantic import BaseModel

from app.core.config import Settings
from worker.llm import LLMError, LLMOutputError, LLMQuotaError, get_provider
from worker.llm import base as base_module
from worker.llm.base import LLMProvider, parse_json_reply
from worker.llm.gemini import GeminiProvider
from worker.llm.groq import GroqProvider
from worker.llm.ollama import OllamaProvider

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


class Verdict(BaseModel):
    result: str
    confidence: float


def mock_http(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class ScriptedProvider(LLMProvider):
    """Returns canned replies in order and records the prompts it was sent."""

    name = "scripted"

    def __init__(self, replies: list[str]):
        super().__init__("test-model", http=mock_http(lambda r: httpx.Response(500)))
        self.replies = replies
        self.prompts: list[str] = []

    async def _complete(self, prompt, schema, images):  # noqa: ANN001
        self.prompts.append(prompt)
        return self.replies.pop(0)


# ---------- retry / validation logic ----------


async def test_valid_reply_is_parsed_and_schema_is_in_prompt() -> None:
    provider = ScriptedProvider(['{"result": "pass", "confidence": 0.9}'])

    verdict = await provider.generate_json("Did the test pass?", Verdict)

    assert verdict == Verdict(result="pass", confidence=0.9)
    assert len(provider.prompts) == 1
    assert "Did the test pass?" in provider.prompts[0]
    assert '"confidence"' in provider.prompts[0]  # JSON schema included


async def test_invalid_json_is_retried_once_with_the_error() -> None:
    provider = ScriptedProvider(["Sure! The result is pass.", '{"result": "pass", "confidence": 1}'])

    verdict = await provider.generate_json("Judge it", Verdict)

    assert verdict.result == "pass"
    assert len(provider.prompts) == 2
    assert "Sure! The result is pass." in provider.prompts[1]  # previous reply echoed back
    assert "not valid JSON" in provider.prompts[1]


async def test_schema_mismatch_is_retried_with_field_errors() -> None:
    provider = ScriptedProvider(['{"result": "pass"}', '{"result": "pass", "confidence": 0.5}'])

    await provider.generate_json("Judge it", Verdict)

    assert "confidence" in provider.prompts[1] and "Field required" in provider.prompts[1]


async def test_second_invalid_reply_raises_llm_output_error() -> None:
    provider = ScriptedProvider(["nope", "still nope"])

    with pytest.raises(LLMOutputError) as info:
        await provider.generate_json("Judge it", Verdict)

    assert info.value.raw == "still nope"
    assert len(provider.prompts) == 2  # exactly one retry


def test_code_fences_are_tolerated() -> None:
    assert parse_json_reply('```json\n{"result": "fail", "confidence": 0.2}\n```', Verdict).result == "fail"


# ---------- providers (HTTP mocked) ----------


async def test_gemini_request_and_response() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["key"] = request.headers.get("x-goog-api-key")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "candidates": [{"content": {"parts": [{"text": '{"result": "pass", "confidence": 0.8}'}]}}],
            "usageMetadata": {"promptTokenCount": 120, "candidatesTokenCount": 9},
        })

    provider = GeminiProvider("test-key", "gemini-3.8-flash", http=mock_http(handler))
    verdict = await provider.generate_json("Judge it", Verdict, images=[PNG])

    assert verdict.confidence == 0.8
    assert seen["url"].endswith("/models/gemini-3.8-flash:generateContent")
    assert "key=" not in seen["url"] and seen["key"] == "test-key"  # key in a header, not the URL
    parts = seen["body"]["contents"][0]["parts"]
    assert parts[1]["inline_data"] == {"mime_type": "image/png", "data": base64.b64encode(PNG).decode()}
    assert seen["body"]["generationConfig"]["responseMimeType"] == "application/json"
    assert (provider.usage.calls, provider.usage.input_tokens, provider.usage.output_tokens) == (1, 120, 9)


async def test_groq_request_and_response() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": '{"result": "fail", "confidence": 0.3}'}}],
            "usage": {"prompt_tokens": 50, "completion_tokens": 7},
        })

    provider = GroqProvider("gsk-test", "llama-vision", http=mock_http(handler))
    verdict = await provider.generate_json("Judge it", Verdict, images=[PNG])

    assert verdict.result == "fail"
    assert seen["auth"] == "Bearer gsk-test"
    assert seen["body"]["response_format"] == {"type": "json_object"}
    content = seen["body"]["messages"][0]["content"]
    assert content[0]["type"] == "text" and content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert provider.usage.input_tokens == 50


async def test_ollama_request_and_response() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "message": {"content": '{"result": "pass", "confidence": 1.0}'},
            "prompt_eval_count": 30, "eval_count": 5,
        })

    provider = OllamaProvider("http://ollama:11434/", "llama3.2-vision", http=mock_http(handler))
    verdict = await provider.generate_json("Judge it", Verdict, images=[PNG])

    assert verdict.result == "pass"
    assert seen["url"] == "http://ollama:11434/api/chat"
    assert seen["body"]["format"]["properties"].keys() == {"result", "confidence"}  # schema as structured output
    assert seen["body"]["messages"][0]["images"] == [base64.b64encode(PNG).decode()]
    assert seen["body"]["stream"] is False


async def test_provider_retries_through_http_too() -> None:
    replies = iter(['{"oops": true}', '{"result": "pass", "confidence": 0.7}'])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": next(replies)}}]})

    provider = GroqProvider("k", "m", http=mock_http(handler))
    assert (await provider.generate_json("Judge it", Verdict)).confidence == 0.7
    assert provider.usage.calls == 2


async def test_http_error_raises_llm_error_without_leaking_key() -> None:
    provider = GeminiProvider("secret-key-123", "m", http=mock_http(lambda r: httpx.Response(403, text="bad key")))

    with pytest.raises(LLMError) as info:
        await provider.generate_json("Judge it", Verdict)

    assert "403" in str(info.value) and "secret-key-123" not in str(info.value)


async def test_missing_api_key_is_a_clear_error() -> None:
    with pytest.raises(LLMError, match="GEMINI_API_KEY is not set"):
        await GeminiProvider("", "m", http=mock_http(lambda r: httpx.Response(200))).generate_json("x", Verdict)


def test_get_provider_uses_llm_provider_setting() -> None:
    http = mock_http(lambda r: httpx.Response(200))
    assert isinstance(get_provider(Settings(llm_provider="gemini"), http=http), GeminiProvider)
    assert isinstance(get_provider(Settings(llm_provider="GROQ"), http=http), GroqProvider)
    ollama = get_provider(Settings(llm_provider="ollama", ollama_model="qwen2.5vl"), http=http)
    assert isinstance(ollama, OllamaProvider) and ollama.model == "qwen2.5vl"
    with pytest.raises(LLMError, match="Unknown LLM provider"):
        get_provider(Settings(llm_provider="openai"), http=http)


# ---------- rate limiting and retries ----------


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    recorded: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        recorded.append(round(seconds, 1))

    monkeypatch.setattr(base_module.asyncio, "sleep", fake_sleep)
    return recorded


GROQ_OK = {"choices": [{"message": {"content": '{"result": "pass", "confidence": 1}'}}]}


async def test_429_is_retried_honouring_retry_after_and_gemini_retry_delay(sleeps: list[float]) -> None:
    replies = iter([
        httpx.Response(429, headers={"Retry-After": "7"}),
        httpx.Response(429, json={"error": {"details": [{"retryDelay": "12s"}]}}),
        httpx.Response(503),
        httpx.Response(200, json=GROQ_OK),
    ])
    provider = GroqProvider("k", "m", http=mock_http(lambda r: next(replies)), max_retries=3)

    assert (await provider.generate_json("Judge it", Verdict)).result == "pass"
    assert sleeps == [7.0, 13.0, 20.0]  # Retry-After, Gemini retryDelay (+1s), exponential backoff (attempt 2)


async def test_gives_up_after_max_retries(sleeps: list[float]) -> None:
    provider = GroqProvider("k", "m", http=mock_http(lambda r: httpx.Response(429, text="slow down")), max_retries=2)

    with pytest.raises(LLMError, match="still rate-limited after 2 retries"):  # an LLMRateLimitError
        await provider.generate_json("Judge it", Verdict)
    assert len(sleeps) == 2


async def test_requests_are_spaced_by_min_interval(sleeps: list[float]) -> None:
    provider = GroqProvider("k", "m", http=mock_http(lambda r: httpx.Response(200, json=GROQ_OK)), min_interval=4.0)

    await provider.generate_json("one", Verdict)
    await provider.generate_json("two", Verdict)

    assert len(sleeps) == 1 and 3.5 <= sleeps[0] <= 4.0  # the second call waited for the interval


async def test_daily_quota_is_not_retried_and_explained(sleeps: list[float]) -> None:
    body = {"error": {"code": 429, "message": "You exceeded your current quota.\n* Quota exceeded for metric …",
                      "details": [{"violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]},
                                  {"retryDelay": "15s"}]}}
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(429, json=body)

    provider = GeminiProvider("k", "gemini-3.8-flash", http=mock_http(handler), max_retries=3)

    with pytest.raises(LLMQuotaError) as info:
        await provider.generate_json("Judge it", Verdict)

    assert len(calls) == 1 and sleeps == []  # no pointless retries
    message = str(info.value)
    assert "daily request quota for gemini-3.8-flash is used up" in message
    assert "You exceeded your current quota." in message and "{" not in message  # readable, not raw JSON


async def test_error_message_is_the_providers_message_not_raw_json() -> None:
    provider = GroqProvider("k", "m", http=mock_http(
        lambda r: httpx.Response(400, json={"error": {"message": "model not found", "type": "invalid_request"}})))

    with pytest.raises(LLMError) as info:
        await provider.generate_json("x", Verdict)

    assert str(info.value) == "groq: HTTP 400: model not found"


async def test_schema_hint_replaces_full_schema_but_reply_is_still_validated() -> None:
    provider = ScriptedProvider(['{"result": "pass"}', '{"result": "pass", "confidence": 0.4}'])

    verdict = await provider.generate_json("Judge it", Verdict, schema_hint='{"result": ..., "confidence": 0-1}')

    assert verdict.confidence == 0.4
    assert '{"result": ..., "confidence": 0-1}' in provider.prompts[0] and '"properties"' not in provider.prompts[0]
    assert "Field required" in provider.prompts[1]  # the hint didn't weaken validation


async def test_groq_tokens_per_day_limit_is_a_daily_quota(sleeps: list[float]) -> None:
    body = {"error": {"message": "Rate limit reached for model `qwen/qwen3.8-27b` on tokens per day (TPD): "
                                 "Limit 200000, Used 198479. Please try again in 3m8.784s.", "type": "tokens"}}
    provider = GroqProvider("k", "qwen/qwen3.8-27b", http=mock_http(lambda r: httpx.Response(429, json=body)),
                            max_retries=5)

    with pytest.raises(LLMQuotaError, match="daily request quota for qwen/qwen3.8-27b is used up"):
        await provider.generate_json("x", Verdict)
    assert sleeps == []


async def test_per_minute_limit_is_still_retried(sleeps: list[float]) -> None:
    replies = iter([httpx.Response(429, json={"error": {"message": "Rate limit reached on tokens per minute (TPM)"}},
                                   headers={"Retry-After": "9"}),
                    httpx.Response(200, json=GROQ_OK)])
    provider = GroqProvider("k", "m", http=mock_http(lambda r: next(replies)))

    assert (await provider.generate_json("x", Verdict)).result == "pass"
    assert sleeps == [9.0]


# ---------- fallback to another provider when tokens run out ----------

TPD = {"error": {"message": "Rate limit reached for model `m` on tokens per day (TPD): Limit 200000, Used 199900."}}
GEMINI_OK = {"candidates": [{"content": {"parts": [{"text": '{"result": "pass", "confidence": 1}'}]}}],
             "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 5}}


@pytest.fixture(autouse=False)
def fresh_fallback_memory() -> None:
    from worker.llm.fallback import FallbackProvider

    FallbackProvider._skip_until.clear()


def counting(response: Callable[[], httpx.Response]) -> tuple[httpx.AsyncClient, list[int]]:
    calls: list[int] = []

    def handler(_r: httpx.Request) -> httpx.Response:
        calls.append(1)
        return response()

    return mock_http(handler), calls


async def test_switches_to_the_next_provider_when_the_daily_quota_is_used_up(
    sleeps: list[float], fresh_fallback_memory: None
) -> None:
    from worker.llm.fallback import FallbackProvider

    groq_http, groq_calls = counting(lambda: httpx.Response(429, json=TPD))
    gemini_http, gemini_calls = counting(lambda: httpx.Response(200, json=GEMINI_OK))
    notes: list[str] = []

    async def on_switch(message: str) -> None:
        notes.append(message)

    llm = FallbackProvider([GroqProvider("k", "m", http=groq_http), GeminiProvider("k", "g", http=gemini_http)],
                           on_switch=on_switch)

    assert (await llm.generate_json("Judge it", Verdict)).result == "pass"
    assert (await llm.generate_json("Again", Verdict)).result == "pass"
    assert len(groq_calls) == 1 and len(gemini_calls) == 2  # groq isn't asked again once it's out of tokens
    assert notes == ["LLM switched from groq to gemini (groq's daily quota is used up)."]
    assert llm.name == "gemini" and llm.used == ["gemini"]
    assert (llm.usage.calls, llm.usage.input_tokens, llm.usage.output_tokens) == (2, 200, 10)

    # A new run in the same worker skips groq straight away (no wasted call, no repeated notice).
    again = FallbackProvider([GroqProvider("k", "m", http=groq_http), GeminiProvider("k", "g", http=gemini_http)])
    assert (await again.generate_json("x", Verdict)).result == "pass"
    assert len(groq_calls) == 1


async def test_switches_back_after_the_cooldown_and_explains_when_all_are_out(
    sleeps: list[float], fresh_fallback_memory: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from worker.llm import fallback

    clock = [1000.0]
    monkeypatch.setattr(fallback.time, "monotonic", lambda: clock[0])
    groq_replies = iter([httpx.Response(429, json=TPD), httpx.Response(200, json=GROQ_OK)])
    notes: list[str] = []

    async def on_switch(message: str) -> None:
        notes.append(message)

    llm = fallback.FallbackProvider([
        GroqProvider("k", "m", http=mock_http(lambda r: next(groq_replies))),
        GeminiProvider("k", "g", http=mock_http(lambda r: httpx.Response(200, json=GEMINI_OK))),
    ], on_switch=on_switch)
    await llm.generate_json("a", Verdict)  # groq out -> gemini
    clock[0] += fallback.QUOTA_COOLDOWN_SECONDS + 1
    await llm.generate_json("b", Verdict)  # cool-down over -> groq again
    assert notes[-1] == "LLM switched back to groq (its quota should be available again)."
    assert llm.used == ["groq", "gemini"]

    out = fallback.FallbackProvider([
        GroqProvider("k", "m2", http=mock_http(lambda r: httpx.Response(429, json=TPD))),
        GeminiProvider("k", "g2", http=mock_http(lambda r: httpx.Response(429, json={"error": {
            "message": "Quota exceeded for metric generate_content_free_tier_requests, limit: 20 PerDay"}}))),
    ])
    with pytest.raises(LLMQuotaError, match="Every configured LLM is out of quota"):
        await out.generate_json("c", Verdict)


async def test_still_rate_limited_after_retries_also_falls_back(sleeps: list[float], fresh_fallback_memory: None) -> None:
    from worker.llm import LLMRateLimitError
    from worker.llm.fallback import FallbackProvider

    slow = GroqProvider("k", "m", http=mock_http(lambda r: httpx.Response(429, text="slow down")), max_retries=1)
    with pytest.raises(LLMRateLimitError):
        await slow.generate_json("x", Verdict)
    llm = FallbackProvider([slow, GeminiProvider("k", "g", http=mock_http(lambda r: httpx.Response(200, json=GEMINI_OK)))])
    assert (await llm.generate_json("x", Verdict)).result == "pass"


def test_get_provider_adds_configured_fallbacks_with_keys() -> None:
    from worker.llm import FallbackProvider

    http = mock_http(lambda r: httpx.Response(200))
    plain = get_provider(Settings(llm_provider="groq", groq_api_key="k", llm_fallbacks=""), http=http)
    assert isinstance(plain, GroqProvider)
    llm = get_provider(Settings(llm_provider="groq", groq_api_key="k", gemini_api_key="g",
                                llm_fallbacks="gemini, groq, ollama"), http=http)
    assert isinstance(llm, FallbackProvider)
    assert [p.name for p in llm.providers] == ["groq", "gemini", "ollama"]  # primary first, no duplicate
    no_key = get_provider(Settings(llm_provider="groq", groq_api_key="k", gemini_api_key="", llm_fallbacks="gemini"),
                          http=http)
    assert isinstance(no_key, GroqProvider)  # a backup without an API key is left out
