"""DB fixtures. Each test runs in a transaction that is rolled back, so any
existing data is left alone (and, thanks to RLS, invisible to test users)."""

import math
import os
import re
import zlib
from dataclasses import dataclass

import pytest
from dotenv import load_dotenv

from kb import rbac
from kb.config import Settings
from kb.db import connect, init_schema
from kb.ingest import Document
from kb.service import KnowledgeBase

load_dotenv()
requires_db = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set")


class HashEmbedder:
    """Deterministic bag-of-words embedder: shared words -> similar vectors. No model download."""

    def __init__(self, dim: int):
        self.dim = dim

    def _embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for word in re.findall(r"\w+", text.lower()):
            vec[zlib.crc32(word.encode()) % self.dim] += 1.0
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]

    def embed_documents(self, texts):
        return [self._embed(t) for t in texts]

    def embed_query(self, text):
        return self._embed(text)


@pytest.fixture
def conn():
    with connect() as c:
        with c.transaction(force_rollback=True):
            yield c


def make_kb(conn, **overrides) -> KnowledgeBase:
    settings = Settings(**overrides)
    init_schema(conn, settings)
    embedder = HashEmbedder(settings.embedding_dim) if settings.vector_enabled else None
    return KnowledgeBase(conn, settings, embedder)


@dataclass
class World:
    kb: KnowledgeBase
    admin: int
    anna: int  # manager of t-be
    bram: int  # member of t-be
    noor: int  # manager of t-nl
    eve: int  # no groups

    def upload(self, user: int, ext_id: str, body: str, groups=(), title=None):
        return self.kb.upload(user, Document(ext_id, title or ext_id, body), groups)


def make_world(conn, **overrides) -> World:
    kb = make_kb(conn, **overrides)
    ids = {
        name: rbac.upsert_user(conn, f"t-{name}@example.com", name, is_admin=(name == "admin"))
        for name in ("admin", "anna", "bram", "noor", "eve")
    }
    be, nl = rbac.upsert_group(conn, "t-be"), rbac.upsert_group(conn, "t-nl")
    rbac.add_member(conn, be, ids["anna"], "manager")
    rbac.add_member(conn, be, ids["bram"], "member")
    rbac.add_member(conn, nl, ids["noor"], "manager")
    return World(kb, **ids)
