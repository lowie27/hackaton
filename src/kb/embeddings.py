"""Embedding models for the vector half of hybrid search."""

from collections.abc import Sequence
from typing import Protocol


class Embedder(Protocol):
    dim: int

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class FastEmbedEmbedder:
    """Local ONNX model via fastembed: no API key, and no document leaves the machine."""

    def __init__(self, model_name: str, dim: int):
        from fastembed import TextEmbedding  # optional dependency: pip install -e '.[vector]'

        self._model = TextEmbedding(model_name)
        self.dim = dim

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [vec.tolist() for vec in self._model.passage_embed(list(texts))]

    def embed_query(self, text: str) -> list[float]:
        return next(iter(self._model.query_embed(text))).tolist()


def to_pgvector(vec: Sequence[float]) -> str:
    """pgvector text literal, used with a ::vector cast (no adapter package needed)."""
    return "[" + ",".join(f"{float(x):.7g}" for x in vec) + "]"
