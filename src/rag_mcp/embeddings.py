"""Embedding providers. All return L2-normalised float vectors."""

from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol

import httpx

from .config import Settings


class Embedder(Protocol):
    name: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def _normalise(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def _timeout(seconds: float) -> httpx.Timeout:
    # A dead host must fail fast so search can fall back to BM25; a slow model load may take long.
    return httpx.Timeout(seconds, connect=3.0)


class OllamaEmbedder:
    """Uses the local Ollama server. Pull a model first: `ollama pull nomic-embed-text`."""

    def __init__(self, base_url: str, model: str, batch_size: int = 32, timeout: float = 120.0):
        self.name = f"ollama:{model}"
        self.model = model
        self.batch_size = batch_size
        self.client = httpx.Client(base_url=base_url.rstrip("/"), timeout=_timeout(timeout))

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]
            r = self.client.post("/api/embed", json={"model": self.model, "input": batch})
            r.raise_for_status()
            out.extend(_normalise(v) for v in r.json()["embeddings"])
        return out


class OpenAIEmbedder:
    """Any OpenAI-compatible /v1/embeddings endpoint."""

    def __init__(self, base_url: str, api_key: str, model: str, batch_size: int = 32, timeout: float = 120.0):
        self.name = f"openai:{model}"
        self.model = model
        self.batch_size = batch_size
        self.client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=_timeout(timeout),
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]
            r = self.client.post("/embeddings", json={"model": self.model, "input": batch})
            r.raise_for_status()
            data = sorted(r.json()["data"], key=lambda d: d["index"])
            out.extend(_normalise(d["embedding"]) for d in data)
        return out


class HashEmbedder:
    """Deterministic bag-of-words hashing. No network, no model.

    Only for tests and offline smoke runs: it captures lexical overlap, not meaning.
    """

    name = "hash"
    _token_re = re.compile(r"\w+", re.UNICODE)

    def __init__(self, dim: int = 256):
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            v = [0.0] * self.dim
            for tok in self._token_re.findall(text.lower()):
                h = int.from_bytes(hashlib.blake2b(tok.encode(), digest_size=4).digest(), "little")
                v[h % self.dim] += 1.0 if (h >> 31) else -1.0
            vectors.append(_normalise(v))
        return vectors


def make_embedder(s: Settings) -> Embedder:
    if s.embed_provider == "ollama":
        return OllamaEmbedder(s.ollama_url, s.embed_model, s.embed_batch_size)
    if s.embed_provider == "openai":
        if not s.openai_api_key:
            raise RuntimeError("RAG_OPENAI_API_KEY is required for RAG_EMBED_PROVIDER=openai")
        return OpenAIEmbedder(s.openai_base_url, s.openai_api_key, s.embed_model, s.embed_batch_size)
    return HashEmbedder()
