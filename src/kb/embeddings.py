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


class Reranker(Protocol):
    def score(self, query: str, texts: Sequence[str]) -> list[float]: ...


class FastEmbedReranker:
    """Cross-encoder: reads query and passage together, so it judges the actual
    answer instead of comparing two separately computed vectors. Too slow for the
    whole corpus, fine for the top candidates."""

    def __init__(self, model_name: str):
        from fastembed.rerank.cross_encoder import TextCrossEncoder  # optional dependency

        self._model = TextCrossEncoder(model_name)

    def score(self, query: str, texts: Sequence[str]) -> list[float]:
        return [float(x) for x in self._model.rerank(query, list(texts))]


def to_pgvector(vec: Sequence[float]) -> str:
    """pgvector text literal, used with a ::vector cast (no adapter package needed)."""
    return "[" + ",".join(f"{float(x):.7g}" for x in vec) + "]"
