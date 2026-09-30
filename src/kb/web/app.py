"""Demo web app: a JSON API over KnowledgeBase plus a single-page UI.

Login is a demo user picker. The chosen user id lives in an HMAC-signed
cookie, and every endpoint takes the acting user from that cookie only,
never from the request body.

Run: uvicorn kb.web.app:app --host 0.0.0.0 --port 8000
"""

import hashlib
import hmac
import os
import re
import secrets
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Literal

from fastapi import Cookie, Depends, FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from kb import rbac
from kb.config import Settings
from kb.context import SIGNALS
from kb.db import connect, init_schema, user_session
from kb.embeddings import FastEmbedEmbedder, FastEmbedReranker
from kb.ingest import Document, load_directory
from kb.retriever import SearchFilters
from kb.service import KnowledgeBase

STATIC = Path(__file__).parent / "static"
DATA = Path(__file__).resolve().parents[3] / "data"
SEED = DATA / "sample" / "seed.json"
UPLOAD_EXAMPLES = DATA / "upload_examples"
COOKIE = "kb_session"
SECRET = (os.environ.get("KB_SESSION_SECRET") or secrets.token_hex(32)).encode()

settings = Settings.from_env()
embedder = None
reranker = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global embedder, reranker
    if settings.vector_enabled:
        embedder = FastEmbedEmbedder(settings.embedding_model, settings.embedding_dim)
    if settings.rerank_enabled:
        reranker = FastEmbedReranker(settings.rerank_model)
    with connect() as conn:
        init_schema(conn, settings)
        kb = KnowledgeBase(conn, settings, embedder, reranker)
        if conn.execute("SELECT count(*) FROM kb.users").fetchone()[0] == 0:
            kb.seed(SEED)
        if embedder:
            kb.embed_missing()
    yield


app = FastAPI(title="SD Worx trusted knowledge demo", lifespan=lifespan)


# --- session -----------------------------------------------------------------


def _sign(user_id: int) -> str:
    sig = hmac.new(SECRET, str(user_id).encode(), hashlib.sha256).hexdigest()
    return f"{user_id}.{sig}"


def current_user(kb_session: str | None = Cookie(default=None)) -> int:
    if kb_session:
        uid, _, sig = kb_session.partition(".")
        if uid.isdigit() and hmac.compare_digest(_sign(int(uid)), kb_session):
            return int(uid)
    raise HTTPException(401, "log in first")


def kb_conn():
    with connect() as conn:
        yield KnowledgeBase(conn, settings, embedder, reranker)


# --- models ------------------------------------------------------------------


class Login(BaseModel):
    email: str


class Filters(BaseModel):
    country: str | None = None
    department: str | None = None
    source: str | None = None
    language: str | None = None
    tags: list[str] = []
    valid_on: date | None = None


class SearchRequest(BaseModel):
    query: str = Field(max_length=500)
    top_k: int = 10
    mode: Literal["bm25", "vector", "hybrid"] | None = None
    context_ranking: bool = True
    rerank: bool = False
    signals: list[str] = list(SIGNALS)
    filters: Filters = Filters()


class UploadRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=50_000)
    groups: list[str] = []
    detect_duplicates: bool = True
    source: str | None = None
    owner: str | None = None
    country: str | None = None
    department: str | None = None
    location: str | None = None
    language: str | None = None
    tags: list[str] = []
    updated_at: date | None = None
    valid_from: date | None = None
    valid_until: date | None = None


# --- endpoints ---------------------------------------------------------------


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/users")
def demo_users(kb: KnowledgeBase = Depends(kb_conn)):
    """The demo login picker. Only public profile fields."""
    rows = kb.conn.execute(
        "SELECT email, display_name, is_admin, country, location, department, position FROM kb.users ORDER BY id"
    ).fetchall()
    keys = ("email", "name", "is_admin", "country", "location", "department", "position")
    return [dict(zip(keys, r)) for r in rows]


@app.post("/api/login")
def login(body: Login, response: Response, kb: KnowledgeBase = Depends(kb_conn)):
    try:
        uid = rbac.user_id_by_email(kb.conn, body.email)
    except LookupError:
        raise HTTPException(404, "unknown user")
    response.set_cookie(COOKIE, _sign(uid), httponly=True, samesite="strict", secure=True)
    return {"ok": True}


@app.post("/api/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE)
    return {"ok": True}


@app.get("/api/me")
def me(uid: int = Depends(current_user), kb: KnowledgeBase = Depends(kb_conn)):
    row = kb.conn.execute(
        "SELECT email, display_name, is_admin, country, location, department, position FROM kb.users WHERE id = %s",
        (uid,),
    ).fetchone()
    if row is None:
        raise HTTPException(401, "log in first")
    groups = kb.conn.execute(
        "SELECT g.name, gm.role FROM kb.group_members gm JOIN kb.groups g ON g.id = gm.group_id "
        "WHERE gm.user_id = %s ORDER BY g.name",
        (uid,),
    ).fetchall()
    keys = ("email", "name", "is_admin", "country", "location", "department", "position")
    return {
        **dict(zip(keys, row)),
        "groups": [{"name": n, "role": r} for n, r in groups],
        "vector_enabled": settings.vector_enabled,
        "rerank_enabled": reranker is not None,
        "signals": list(SIGNALS),
    }


@app.post("/api/search")
def search(req: SearchRequest, uid: int = Depends(current_user), kb: KnowledgeBase = Depends(kb_conn)):
    signals = [s for s in req.signals if s in SIGNALS]
    try:
        hits = kb.search(
            uid, req.query, req.top_k, req.mode, SearchFilters(**req.filters.model_dump()),
            context_ranking=req.context_ranking, signals=signals, rerank=req.rerank,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return [h.__dict__ for h in hits]


@app.get("/api/documents")
def documents(uid: int = Depends(current_user), kb: KnowledgeBase = Depends(kb_conn)):
    """Everything this user may read: row-level security does the filtering."""
    with user_session(kb.conn, uid):
        cur = kb.conn.execute(
            """
            SELECT d.id, d.title, d.source, d.owner, d.country, d.department, d.location, d.language, d.tags,
                   d.updated_at, d.valid_from, d.valid_until, d.uploader_position, d.uploader_is_manager,
                   array(SELECT g.name FROM kb.document_groups dg JOIN kb.groups g ON g.id = dg.group_id
                         WHERE dg.doc_id = d.id ORDER BY g.name) AS groups
            FROM kb.documents d ORDER BY d.id
            """
        )
        cols = [c.name for c in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    total = kb.conn.execute("SELECT count(*) FROM kb.documents").fetchone()[0]
    return {"documents": rows, "total_in_system": total}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:80] or "doc"


@app.post("/api/upload")
def upload(req: UploadRequest, uid: int = Depends(current_user), kb: KnowledgeBase = Depends(kb_conn)):
    meta = {k: v for k, v in req.model_dump(exclude={"title", "body", "groups", "detect_duplicates"}).items() if v}
    meta = {k: (v.isoformat() if isinstance(v, date) else v) for k, v in meta.items()}
    doc = Document(f"upload/{uid}/{_slug(req.title)}", req.title, req.body, meta)
    try:
        result = kb.upload(uid, doc, req.groups, detect_duplicates=req.detect_duplicates)
    except rbac.PermissionDenied as e:
        raise HTTPException(403, str(e))
    except (LookupError, ValueError) as e:
        raise HTTPException(400, str(e))
    return {"doc_id": result.doc_id, "similar": [s.__dict__ for s in result.similar]}


@app.get("/api/upload-examples")
def upload_examples(uid: int = Depends(current_user)):
    """Two documents that are not in the database: one unique, one near-copy of an existing one."""
    return [
        {"file": Path(d.external_id).stem, "title": d.title, "body": d.body.strip(), **d.meta}
        for d in load_directory(UPLOAD_EXAMPLES)
    ]


@app.get("/api/notifications")
def notifications(uid: int = Depends(current_user), kb: KnowledgeBase = Depends(kb_conn)):
    return [n.__dict__ for n in kb.notifications(uid)]


@app.post("/api/notifications/{notification_id}/read")
def read_notification(notification_id: int, uid: int = Depends(current_user), kb: KnowledgeBase = Depends(kb_conn)):
    if not kb.mark_notification_read(uid, notification_id):
        raise HTTPException(404, "not found")
    return {"ok": True}


@app.post("/api/admin/reset")
def reset(uid: int = Depends(current_user), kb: KnowledgeBase = Depends(kb_conn)):
    """Admins only: wipe everything and load the demo data again."""
    if not rbac.is_admin(kb.conn, uid):
        raise HTTPException(403, "admins only")
    with kb.conn.transaction():
        kb.conn.execute("TRUNCATE kb.notifications, kb.similarity_alerts, kb.document_groups, kb.chunks, "
                        "kb.documents, kb.group_members, kb.groups, kb.users RESTART IDENTITY CASCADE")
    kb.seed(SEED)
    if embedder:
        kb.embed_missing()
    return {"ok": True}
