import base64
from typing import Any

from pydantic import BaseModel

from worker.llm.base import LLMError, LLMProvider, image_mime

GROQ_API = "https://api.groq.com/openai/v1"


class GroqProvider(LLMProvider):
    """Groq's OpenAI-compatible chat completions API in JSON mode (vision models accept images)."""

    name = "groq"

    def __init__(self, api_key: str, model: str, **options: Any):  # options: see LLMProvider.__init__
        super().__init__(model, **options)
        self.api_key = api_key

    async def _complete(self, prompt: str, schema: type[BaseModel], images: list[bytes]) -> str:
        if not self.api_key:
            raise LLMError("groq: GROQ_API_KEY is not set")
        content: str | list[dict] = prompt
        if images:
            content = [{"type": "text", "text": prompt}] + [
                {"type": "image_url",
                 "image_url": {"url": f"data:{image_mime(img)};base64,{base64.b64encode(img).decode()}"}}
                for img in images
            ]
        data = await self._post(
            f"{GROQ_API}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json_body={
                "model": self.model,
                "messages": [{"role": "user", "content": content}],
                "response_format": {"type": "json_object"},
                "temperature": 0.2,
            },
        )
        usage = data.get("usage", {})
        self.usage.calls += 1
        self.usage.input_tokens += usage.get("prompt_tokens", 0)
        self.usage.output_tokens += usage.get("completion_tokens", 0)
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError) as exc:
            raise LLMError("groq: response had no choices") from exc
