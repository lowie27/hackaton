"""Runtime settings, read from environment variables (and .env)."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv


def _flag(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    # Hybrid search switch: off = BM25 only, no embedding model or pgvector needed.
    vector_enabled: bool = False
    # Multilingual so Dutch/French/English documents land in one vector space.
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    embedding_dim: int = 384
    rrf_k: int = 60
    # Vector hits below this cosine similarity are dropped (avoids noise when nothing matches).
    min_vector_similarity: float = 0.25
    # An upload at or above either score raises a near-duplicate alert.
    duplicate_lexical_threshold: float = 0.5
    duplicate_vector_threshold: float = 0.9

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        d = cls()
        return cls(
            vector_enabled=_flag("KB_VECTOR_ENABLED", d.vector_enabled),
            embedding_model=os.environ.get("KB_EMBEDDING_MODEL", d.embedding_model),
            embedding_dim=int(os.environ.get("KB_EMBEDDING_DIM", d.embedding_dim)),
            duplicate_lexical_threshold=float(
                os.environ.get("KB_DUPLICATE_LEXICAL_THRESHOLD", d.duplicate_lexical_threshold)
            ),
            duplicate_vector_threshold=float(
                os.environ.get("KB_DUPLICATE_VECTOR_THRESHOLD", d.duplicate_vector_threshold)
            ),
        )
