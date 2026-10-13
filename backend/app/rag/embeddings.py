"""Text embeddings for the RAG memory, behind LangChain's `Embeddings` interface.

- MiniLMEmbeddings: all-MiniLM-L6-v2 (384 dimensions) run locally with ONNX Runtime on the CPU. No API key, no
  quota (the LLM free tiers are already tight), and the same text always gives the same vector. The ~80 MB model
  is downloaded on first use (the Docker images bake it in).
- HashingEmbeddings: hashed bag of words. Not semantic, but needs no download: for tests and offline use.
"""

import hashlib
import math
import re
import threading
from typing import Any

from langchain_core.embeddings import Embeddings

from app.core.config import Settings


class MiniLMEmbeddings(Embeddings):
    def __init__(self) -> None:
        self._model: Any = None
        self._lock = threading.Lock()

    def _load(self) -> Any:  # noqa: ANN401 - chromadb's embedding function
        with self._lock:
            if self._model is None:
                from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2  # noqa: PLC0415 - heavy import

                self._model = ONNXMiniLM_L6_V2(preferred_providers=["CPUExecutionProvider"])
        return self._model

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return [[float(x) for x in vector] for vector in self._load()(texts)]

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


_WORD = re.compile(r"[a-z0-9]+")


class HashingEmbeddings(Embeddings):
    """Words (and their first 5 letters, a crude stem) hashed into `dimensions` buckets, L2-normalised."""

    def __init__(self, dimensions: int = 256) -> None:
        self.dimensions = dimensions

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for word in _WORD.findall(text.lower()):
            for token in {word, word[:5]}:
                digest = hashlib.md5(token.encode(), usedforsecurity=False).digest()
                vector[int.from_bytes(digest[:4], "big") % self.dimensions] += 1.0 if digest[4] % 2 else -1.0
        norm = math.sqrt(sum(x * x for x in vector)) or 1.0
        return [x / norm for x in vector]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


def make_embeddings(settings: Settings) -> Embeddings:
    name = settings.embedding_model.strip().lower()
    if name == "minilm":
        return MiniLMEmbeddings()
    if name == "hashing":
        return HashingEmbeddings()
    raise ValueError(f"Unknown EMBEDDING_MODEL {settings.embedding_model!r}; use minilm or hashing")
