"""Expert directory: who to ask when the documents are not enough.

The people live in their own database (kb_experts), separate from the
documents. They are ranked the same way as documents, and explained the
same way: BM25 in SQL, meaning (embeddings), an optional cross-encoder
rerank, then context signals with reasons and warnings (same country or
department, owns a document in your results, out of office, inactive).

The directory is organisation-wide, so every signed-in user may search it;
it holds work profiles only.
"""

import json
import math
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
from psycopg import sql
from dotenv import load_dotenv
from psycopg.rows import dict_row

from kb.config import Settings
from kb.context import UserContext
from kb.embeddings import Embedder, Reranker
from kb.retriever import reciprocal_rank_fusion

SQL_DIR = Path(__file__).resolve().parents[2] / "sql" / "experts"

# Context adjustments for people, same scale as kb.context.
EXPERT_COUNTRY_MATCH = 0.3
EXPERT_COUNTRY_OTHER = -0.3
EXPERT_DEPARTMENT_MATCH = 0.2
EXPERT_LOCATION_MATCH = 0.1
OWNS_RESULT = 0.5
OWNS_TRUSTED_ANSWER = 0.3  # extra when they own the most trustworthy version
AWAY = -0.6
ACTIVE = 0.1
INACTIVE = -0.3
EXPERIENCED = 0.1
ACTIVE_DAYS, INACTIVE_DAYS, EXPERIENCED_ANSWERS = 14, 180, 30
MIN_FACTOR = 0.1
RELEVANT = 0.3  # rerank score from which a search result counts as context for the experts


@dataclass
class ExpertHit:
    id: int
    name: str
    email: str
    position: str | None
    department: str | None
    country: str | None
    location: str | None
    languages: list[str]
    tags: list[str]
    owns: list[str]
    away_until: date | None
    last_active: date | None
    answered: int
    score: float
    relevance: float
    bm25_rank: int | None = None
    matched_terms: list[str] = field(default_factory=list)
    vector_rank: int | None = None
    rerank_score: float | None = None
    trust: float = 1.0
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def database_url() -> str:
    """EXPERTS_DATABASE_URL, or DATABASE_URL with the database name replaced by kb_experts."""
    load_dotenv()
    url = os.environ.get("EXPERTS_DATABASE_URL")
    if url:
        return url
    base = os.environ.get("DATABASE_URL")
    if not base:
        raise RuntimeError("DATABASE_URL is not set (copy .env.example to .env)")
    parts = urlsplit(base)
    return urlunsplit(parts._replace(path="/kb_experts"))


def connect() -> psycopg.Connection:
    return psycopg.connect(database_url(), autocommit=True)


def ensure_database(main: psycopg.Connection) -> None:
    """Create the experts database next to the documents database if it does not exist."""
    name = urlsplit(database_url()).path.lstrip("/")
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", name):
        raise ValueError(f"unexpected experts database name: {name!r}")
    if main.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone() is None:
        main.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))


def init_schema(conn: psycopg.Connection) -> None:
    with conn.transaction():
        for path in sorted(SQL_DIR.glob("*.sql")):
            conn.execute(path.read_text(encoding="utf-8"))


def _search_text(p: dict) -> str:
    return " ".join([p["name"], p.get("position") or "", p["expertise"], " ".join(p.get("tags", []))])


def seed(conn: psycopg.Connection, spec_path: Path, embedder: Embedder | None = None) -> int:
    people = json.loads(spec_path.read_text(encoding="utf-8"))
    vectors = embedder.embed_documents([_search_text(p) for p in people]) if embedder else [None] * len(people)
    with conn.transaction(), conn.cursor() as cur:
        for p, vec in zip(people, vectors):
            cur.execute(
                """
                INSERT INTO experts.people (email, name, position, department, country, location, languages,
                                            expertise, tags, owns, away_until, last_active, answered, search_text, embedding)
                VALUES (%(email)s, %(name)s, %(position)s, %(department)s, %(country)s, %(location)s, %(languages)s,
                        %(expertise)s, %(tags)s, %(owns)s, %(away_until)s, %(last_active)s, %(answered)s,
                        %(search_text)s, %(embedding)s)
                ON CONFLICT (email) DO UPDATE SET
                    name = EXCLUDED.name, position = EXCLUDED.position, department = EXCLUDED.department,
                    country = EXCLUDED.country, location = EXCLUDED.location, languages = EXCLUDED.languages,
                    expertise = EXCLUDED.expertise, tags = EXCLUDED.tags, owns = EXCLUDED.owns,
                    away_until = EXCLUDED.away_until, last_active = EXCLUDED.last_active,
                    answered = EXCLUDED.answered, search_text = EXCLUDED.search_text, embedding = EXCLUDED.embedding
                """,
                {
                    "email": p["email"], "name": p["name"], "position": p.get("position"),
                    "department": p.get("department"), "country": p.get("country"), "location": p.get("location"),
                    "languages": p.get("languages", []), "expertise": p["expertise"], "tags": p.get("tags", []),
                    "owns": p.get("owns", []), "away_until": p.get("away_until"), "last_active": p.get("last_active"),
                    "answered": p.get("answered", 0), "search_text": _search_text(p),
                    "embedding": list(vec) if vec is not None else None,
                },
            )
    return len(people)


def _cos(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def assess_expert(person: dict, user: UserContext, today: date, owners: dict[str, str], trusted_owner: str | None,
                  countries: set[str]) -> tuple[float, list[str], list[str]]:
    """Context factor, reasons and warnings for one person.

    owners maps a document owner name to the title of a result it owns; trusted_owner is the
    owner of the most trustworthy version when results disagree; countries are the user's
    country plus the countries of the top results.
    """
    adj, reasons, warnings = 0.0, [], []
    country = person.get("country")
    if country and countries:
        if country.upper() in countries:
            adj += EXPERT_COUNTRY_MATCH
            reasons.append(f"works in {country}")
        else:
            adj += EXPERT_COUNTRY_OTHER
            warnings.append(f"works in {country}, the question is about {', '.join(sorted(countries))}")
    elif not country:
        reasons.append("group-wide role")
    if person.get("department") and user.department and person["department"].lower() == user.department.lower():
        adj += EXPERT_DEPARTMENT_MATCH
        reasons.append(f"same department ({person['department']})")
    if person.get("location") and user.location and person["location"].lower() == user.location.lower():
        adj += EXPERT_LOCATION_MATCH
        reasons.append(f"same office ({person['location']})")

    owned = [o for o in person.get("owns") or [] if o in owners]
    if owned:
        adj += OWNS_RESULT
        reasons.append(f"owns '{owners[owned[0]]}' in your results")
        if trusted_owner in owned:
            adj += OWNS_TRUSTED_ANSWER
            reasons.append("owns the most trustworthy version, so can confirm it")

    away = person.get("away_until")
    if away and away >= today:
        adj += AWAY
        warnings.append(f"out of office until {away.isoformat()}")
    last = person.get("last_active")
    if last:
        days = (today - last).days
        if days <= ACTIVE_DAYS:
            adj += ACTIVE
            reasons.append("active this week" if days <= 7 else f"active {days} days ago")
        elif days >= INACTIVE_DAYS:
            adj += INACTIVE
            warnings.append(f"not active for {days // 30} months")
    if person.get("answered", 0) >= EXPERIENCED_ANSWERS:
        adj += EXPERIENCED
        reasons.append(f"answered {person['answered']} questions")
    return max(MIN_FACTOR, 1.0 + adj), reasons, warnings


def find_experts(
    conn: psycopg.Connection,
    settings: Settings,
    query: str,
    user: UserContext,
    hits: Sequence = (),
    disputes: Sequence = (),
    top_k: int = 3,
    embedder: Embedder | None = None,
    reranker: Reranker | None = None,
    today: date | None = None,
) -> list[ExpertHit]:
    """Rank people for a question, using the search results as extra context."""
    today = today or date.today()
    pool = 20
    bm25 = conn.execute("SELECT person_id, score, matched_terms FROM experts.bm25_search(%s, %s)", (query, pool)).fetchall()
    with conn.cursor(row_factory=dict_row) as cur:
        people = {p["id"]: p for p in cur.execute("SELECT * FROM experts.people")}

    rankings = [[pid for pid, _, _ in bm25]]
    vector_ranks: dict[int, int] = {}
    if embedder is not None:
        q = embedder.embed_query(query)
        sims = sorted(
            ((pid, _cos(q, p["embedding"])) for pid, p in people.items() if p["embedding"]),
            key=lambda r: -r[1],
        )
        sims = [(pid, s) for pid, s in sims if s >= settings.min_vector_similarity][:pool]
        rankings.append([pid for pid, _ in sims])
        vector_ranks = {pid: rank for rank, (pid, _) in enumerate(sims, start=1)}
    fused = reciprocal_rank_fusion(rankings, settings.rrf_k) if len(rankings) > 1 else {pid: s for pid, s, _ in bm25}

    # People who own a document in the results are candidates even without a text match.
    # Only results that answer the question say something about who to ask.
    hits = [h for h in hits if getattr(h, "rerank_score", None) is None or h.rerank_score >= RELEVANT]
    owners = {}
    for h in hits[:5]:
        if getattr(h, "owner", None):
            owners.setdefault(h.owner, h.title)
    trusted_owner = None
    if disputes:
        best_title = disputes[0].claims[0].title
        trusted_owner = next((h.owner for h in hits if h.title == best_title and getattr(h, "owner", None)), None)
    floor = min(fused.values(), default=0.01) / 2
    for pid, p in people.items():
        if pid not in fused and set(p["owns"] or []) & owners.keys():
            fused[pid] = floor

    candidates = sorted(fused, key=lambda pid: (-fused[pid], pid))[:pool]
    relevance = dict(fused)
    rerank = {}
    if reranker is not None and candidates:
        logits = reranker.score(query, [people[pid]["search_text"] for pid in candidates])
        rerank = {pid: 1 / (1 + math.exp(-x)) for pid, x in zip(candidates, logits)}
        relevance = rerank

    countries = {c.upper() for c in [user.country, *(getattr(h, "country", None) for h in hits[:3])] if c}
    bm25_by_id = {pid: (rank, terms) for rank, (pid, _, terms) in enumerate(bm25, start=1)}
    out = []
    for pid in candidates:
        p = people[pid]
        trust, reasons, warnings = assess_expert(p, user, today, owners, trusted_owner, countries)
        bm25_rank, terms = bm25_by_id.get(pid, (None, []))
        out.append(ExpertHit(
            id=pid, name=p["name"], email=p["email"], position=p["position"], department=p["department"],
            country=p["country"], location=p["location"], languages=p["languages"], tags=p["tags"], owns=p["owns"],
            away_until=p["away_until"], last_active=p["last_active"], answered=p["answered"],
            score=relevance[pid] * trust, relevance=relevance[pid], bm25_rank=bm25_rank, matched_terms=terms,
            vector_rank=vector_ranks.get(pid), rerank_score=rerank.get(pid), trust=trust,
            reasons=reasons, warnings=warnings,
        ))
    out.sort(key=lambda e: (-e.score, e.id))
    return out[:top_k]
