import logging
import math
import time
from typing import Protocol

from google import genai
from google.genai import errors, types
from langchain.chat_models import init_chat_model

log = logging.getLogger(__name__)


def make_chat_model(spec: str, thinking_level: str = ""):
    """spec con formato `proveedor:modelo` (p. ej. google_genai:gemini-2.5-flash).

    thinking_level (Gemini 3+: minimal | low | medium | high): vacío = el valor por defecto del modelo.
    Es la principal palanca de latencia del generador.
    """
    extra = {"thinking_level": thinking_level} if thinking_level else {}
    return init_chat_model(spec, temperature=0, **extra)


class Embedder(Protocol):
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...
    def embed_queries(self, texts: list[str]) -> list[list[float]]: ...


def l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    return [x / norm for x in vec] if norm else vec


class GeminiEmbedder:
    """gemini-embedding-001 a 768 dims. Por debajo de 3072 Google no normaliza: lo hacemos aquí."""

    def __init__(self, api_key: str, model: str = "gemini-embedding-001", dim: int = 768,
                 batch_size: int = 50, max_retries: int = 5):
        self._client = genai.Client(api_key=api_key or None)
        self._model, self._dim = model, dim
        self._batch, self._retries = batch_size, max_retries

    def _embed(self, texts: list[str], task_type: str) -> list[list[float]]:
        cfg = types.EmbedContentConfig(task_type=task_type, output_dimensionality=self._dim)
        for attempt in range(self._retries + 1):
            try:
                res = self._client.models.embed_content(model=self._model, contents=texts, config=cfg)
                return [l2_normalize(list(e.values)) for e in res.embeddings]
            except errors.APIError as exc:
                if exc.code != 429 or attempt == self._retries:
                    raise
                wait = 2**attempt
                log.warning("Embeddings 429, reintento en %ss", wait)
                time.sleep(wait)
        raise RuntimeError("unreachable")

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self._batch):
            out += self._embed(texts[i : i + self._batch], "RETRIEVAL_DOCUMENT")
        return out

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text], "RETRIEVAL_QUERY")[0]

    def embed_queries(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts, "RETRIEVAL_QUERY")
