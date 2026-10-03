import base64
from typing import Any

from pydantic import BaseModel

from worker.llm.base import LLMError, LLMProvider, image_mime

GEMINI_API = "https://generativelanguage.googleapis.com/v1beta"


class GeminiProvider(LLMProvider):
    """Google Gemini via the REST generateContent API (supports images)."""

    name = "gemini"

    def __init__(self, api_key: str, model: str, **options: Any):  # options: see LLMProvider.__init__
        super().__init__(model, **options)
        self.api_key = api_key

    async def _complete(self, prompt: str, schema: type[BaseModel], images: list[bytes]) -> str:
        if not self.api_key:
            raise LLMError("gemini: GEMINI_API_KEY is not set")
        parts: list[dict] = [{"text": prompt}]
        parts += [
            {"inline_data": {"mime_type": image_mime(img), "data": base64.b64encode(img).decode()}} for img in images
        ]
        data = await self._post(
            f"{GEMINI_API}/models/{self.model}:generateContent",
            headers={"x-goog-api-key": self.api_key},
            json_body={
                "contents": [{"role": "user", "parts": parts}],
                "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2},
            },
        )
        usage = data.get("usageMetadata", {})
        self.usage.calls += 1
        self.usage.input_tokens += usage.get("promptTokenCount", 0)
        self.usage.output_tokens += usage.get("candidatesTokenCount", 0)
        try:
            return "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
        except (KeyError, IndexError) as exc:
            reason = data.get("promptFeedback", {}).get("blockReason") or "no candidates"
            raise LLMError(f"gemini: empty response ({reason})") from exc
