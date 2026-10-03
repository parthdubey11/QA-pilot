import base64
from typing import Any

from pydantic import BaseModel

from worker.llm.base import LLMError, LLMProvider


class OllamaProvider(LLMProvider):
    """A local Ollama server (/api/chat). The JSON schema is passed as `format` for structured output."""

    name = "ollama"

    def __init__(self, base_url: str, model: str, **options: Any):  # options: see LLMProvider.__init__
        options.setdefault("timeout", 180.0)  # local models can be slow
        super().__init__(model, **options)
        self.base_url = base_url.rstrip("/")

    async def _complete(self, prompt: str, schema: type[BaseModel], images: list[bytes]) -> str:
        message: dict = {"role": "user", "content": prompt}
        if images:
            message["images"] = [base64.b64encode(img).decode() for img in images]
        data = await self._post(
            f"{self.base_url}/api/chat",
            json_body={
                "model": self.model,
                "messages": [message],
                "format": schema.model_json_schema(),
                "stream": False,
                "options": {"temperature": 0.2},
            },
        )
        self.usage.calls += 1
        self.usage.input_tokens += data.get("prompt_eval_count", 0)
        self.usage.output_tokens += data.get("eval_count", 0)
        try:
            return data["message"]["content"]
        except KeyError as exc:
            raise LLMError("ollama: response had no message") from exc
