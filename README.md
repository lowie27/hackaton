# Trusted Knowledge: SD Worx "Unlock the Knowledge Within"

Tectonic Hackathon 2026, SD Worx case. Live demo: https://temp.dekeyser.ai (synthetic data only).

A payroll consultant asks how much double holiday pay is and finds three answers: 92% in the official policy, 93% in a copy pasted from Teams, 85% in an old intranet FAQ. This proof of concept is a knowledge search that shows **which answer to trust and why**, and **who to ask** when the documents are not enough. Every ranking decision is a sentence you can read, and every rule can be switched off to see its effect.

## What it does

- **Hybrid search that explains itself.** BM25 is computed inside PostgreSQL, pgvector adds semantic matches with a multilingual model (Dutch and French questions find English documents), and both rankings are fused with reciprocal rank fusion. Every hit shows *why* it matched: BM25 rank and matched terms, vector rank and cosine.
- **Rerank for accuracy.** A cross-encoder (`jinaai/jina-reranker-v2-base-multilingual`, local via fastembed) reads the question together with each of the top 20 candidates and re-scores them: it judges whether the passage actually answers the question. It only sees the shortlist the user may read.
- **Context-aware ranking.** Documents carry country, site, department, language, tags, a validity period, source, owner and a snapshot of the uploader's position; users have a profile. Results that apply to the person asking move up, and every hit lists reasons ("applies to your country (BE)", "official policy document") and warnings ("expired on 2024-12-31", "no accountable owner", "applies to NL, you work in BE"). Hard filters (country, department, source, language, tag, in force on a date) run in SQL before top-k. *"What is current? What applies in this context?"*
- **Where results disagree.** Facts (percentages, euro amounts, numbers of days/weeks/months/years) are extracted from the results and compared: same kind of fact, sentences about the same thing, same country (or group-wide). When values differ, the UI shows every value, which document says it, the quoted sentence, and which one to trust: the claim from the most trustworthy source, not the best text match. A result that contradicts a more trustworthy one loses 40%, with that as a warning. Different countries are never compared (BE 92% vs NL 8% is a different context). Rule-based, no language model. *"Which answer should a person trust?"*
- **Ask a person when the documents are not enough.** The whole result list is judged: no result really answers the question, the best result cannot be relied on, sources disagree without a clear winner, or near-identical copies exist. Then the page recommends people from an expert directory in a **separate database** (`kb_experts`), ranked the same way as documents: BM25 and meaning on their expertise, rerank, then context signals with reasons and warnings (same country, department and office, owns a document in your results, owns the most trustworthy version, out of office, inactive, experience). *"Who has relevant expertise? Where are the gaps?"*
- **Near-duplicate alerts on upload.** Each upload is compared with all documents (term overlap, plus embedding similarity when vectors are on). The uploader, the other document's uploader and the group managers are notified, without naming documents they cannot read. *"Detect: duplicated, outdated knowledge."*
- **Access control in the database.** Users see only documents shared with their groups (or uploaded by them). PostgreSQL row-level security enforces this for every query, so hidden documents never reach a result list, not even as a slot in the top-k.

## How to run

You need Python 3.11+ and Docker. Run everything from the repo root.

### Web UI (quickest)

```bash
cp .env.example .env            # fill in the database password in BOTH places, and set KB_SESSION_SECRET
                                # and KB_ADMIN_PASSWORD (uncomment them) for admin login and reset
docker compose up -d --build    # database + web UI on http://localhost:8000
```

The first start downloads the embedding model (about 200 MB) and the reranker (about 1.1 GB); both are cached in a Docker volume. Set `KB_WEB_VECTOR_ENABLED=false` and `KB_WEB_RERANK_ENABLED=false` in `.env` for a small keyword-only setup. The demo data is loaded on the first start.

Pick a demo user, then:

- **Search**: results with *why it matched*, metadata, green *why you can rely on it* and amber *be careful* signals, a box when results disagree, and people to ask when the documents are not enough.
- **Sidebar**: switch the search logic (Keyword / Meaning / Hybrid), the rerank, context ranking, each of the nine context signals, the disagreement check, people recommendations, hard filters, and what is displayed.
- **Compare logics**: one question side by side under two setups (relevance only vs context-aware, keyword vs hybrid, without vs with rerank, ...) with rank changes marked.
- **Upload**: add a document with metadata and a duplicate check. Two examples that are not in the database (`data/upload_examples/`) fill the form: a unique one (bike leasing; an official policy, so upload it as Eva, the hr-be manager) and a near-copy of the home-working allowance policy that triggers a duplicate alert and a disagreement.
- **What I can see**: the documents your groups give you access to. **Notifications**: duplicate alerts.
- **Admin** (needs `KB_ADMIN_PASSWORD`): *Reset demo data*.

Demo users (see `data/sample/seed.json`): **Bram** (payroll consultant, BE, Antwerp), **Anna** (payroll BE lead), **Noor** (payroll NL lead), **Eva** (HR business partner, BE), **Demo Admin** (sees everything; password required).

Hosting behind a reverse proxy on an external `edge` Docker network: `docker compose -f docker-compose.yml -f docker-compose.edge.yml up -d`, with a site block like `deploy/Caddyfile.example`.

### Command line and tests

```bash
cp .env.example .env            # as above
docker compose up -d db         # only the database
python -m venv .venv
. .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python -m kb init               # tables, search index, row-level security (+ pgvector), and the kb_experts database
python -m kb seed               # demo users, groups, documents and experts from data/sample/

# Bram searches: 2025 policy first, the Teams copy and the expired 2019 FAQ flagged
python -m kb search "double holiday pay" --as bram@example.com
# Only documents in force on a date, with a tag
python -m kb search "holiday pay" --as bram@example.com --valid-on 2026-09-30 --tag "holiday pay"
# People to ask, ranked like documents
python -m kb experts "can I still order a hybrid company car" --as bram@example.com
# Duplicate alerts Anna received
python -m kb notifications --as anna@example.com
python -m kb --help             # all commands

pytest                          # 84 tests; needs the database
```

The web UI also runs outside Docker: `uvicorn kb.web.app:app --port 8000`.

### Options

- **Vectors.** `KB_VECTOR_ENABLED=true|false` for the library and CLI (the web container uses `KB_WEB_VECTOR_ENABLED`, on by default). After turning them on for existing data, run `python -m kb init` and then `python -m kb embed`. Default model: `paraphrase-multilingual-MiniLM-L12-v2`, local via fastembed. For BM25 only, `pip install -e '.[web,dev]'` skips fastembed.
- **Rerank.** `KB_RERANK_ENABLED=true` (needs the vector dependencies). `KB_RERANK_MODEL` picks another fastembed cross-encoder, e.g. `Xenova/ms-marco-MiniLM-L-6-v2` (80 MB, English only); `KB_RERANK_CANDIDATES` (default 20) sets how many results are re-scored.
- **Context ranking.** `KB_CONTEXT_RANKING=false` for pure relevance ranking. The weights are in `src/kb/context.py`.
- **Document metadata** comes from frontmatter (`title, source, owner, country, location, department, language, tags, updated_at, valid_from, valid_until`) or the `meta` dict of `Document`.

## Architecture

```
upload ─► KnowledgeBase.upload ─► permission checks (rbac.py: groups, replace, authority fields)
                     │           ─► kb.documents / kb.chunks (+ embedding) ─► CALL kb.refresh_index()
                     ▼
          alerts.detect_and_notify (term overlap + cosine, all documents)
                     ─► kb.similarity_alerts ─► kb.notifications (per recipient, redacted)

search ─► KnowledgeBase.search ─► user_session: SET LOCAL ROLE kb_app + kb.user_id (row-level security)
                                   ├─ kb.bm25_search(q)                  (SQL)
                                   └─ chunks ORDER BY embedding <=> q    (pgvector)
                                   ─► reciprocal rank fusion
                                   ─► cross-encoder rerank of the top 20 (optional)
                                   ─► context ranking (kb.context)
                                   ─► where results disagree (kb.conflicts), demote contradicted
                                   ─► ask a person? (kb.escalation) ─► experts (kb_experts database)
```

| File | What |
|---|---|
| `src/kb/service.py` | **`KnowledgeBase`: the API for the UI** |
| `src/kb/web/` | FastAPI JSON API and the single-page demo UI |
| `src/kb/retriever.py` | hybrid retrieval, fusion, rerank, metadata filters |
| `src/kb/context.py` | context-aware ranking with reasons and warnings |
| `src/kb/conflicts.py` | where results disagree, and which claim to trust |
| `src/kb/escalation.py` | when a person should answer instead of the documents |
| `src/kb/experts.py` | expert directory in its own database, ranked like documents |
| `src/kb/alerts.py` | duplicate detection and notifications |
| `src/kb/rbac.py` | users, groups and write permission checks |
| `sql/001_bm25.sql` | chunks, inverted-index views, `kb.bm25_search` (Okapi BM25, Lucene IDF) |
| `sql/002_rbac.sql` | users, groups, memberships (member/manager), document shares |
| `sql/003_alerts.sql` | similarity alerts and notifications |
| `sql/004_security.sql` | `kb_app` role, grants, row-level security policies |
| `sql/005_metadata.sql` | user profiles and document context metadata |
| `sql/optional/vector.sql` | pgvector columns and HNSW indexes (only when vectors are on) |
| `sql/experts/001_people.sql` | expert directory and its BM25 function (`kb_experts` database) |

## Using it from code

```python
from datetime import date

from kb import KnowledgeBase, SearchFilters
from kb.db import connect
from kb.ingest import Document

kb = KnowledgeBase(connect())                     # settings from .env
hits = kb.search(user_id, "double holiday pay")   # SearchHit list: .reasons, .warnings, .disagreements
hits = kb.search(user_id, "holiday pay", filters=SearchFilters(country="BE", valid_on=date.today()))
result = kb.upload(user_id, Document("hr/leave.md", "Leave policy", text, {"country": "BE"}), ["payroll-be"])
result.similar                                    # near-duplicates the uploader may see
kb.notifications(user_id); kb.mark_notification_read(user_id, notification_id)
```

The caller must authenticate the user and pass *their* id, never an id from the request body. `KnowledgeBase` raises `rbac.PermissionDenied` for forbidden writes.

## Security

What the Aikido AI Code Audit looks for, and how this code handles it (tests in `tests/test_rbac.py` and `tests/test_web_security.py`):

- **Authentication.** The session is the user id plus an expiry, HMAC-SHA256 signed (`KB_SESSION_SECRET`), in an HttpOnly, Secure, SameSite=Strict cookie that expires after 8 hours; forged or expired cookies get 401. Admin accounts never log in without `KB_ADMIN_PASSWORD` (constant-time compare); unset means no admin login and no reset. The passwordless picker for non-admin demo users is a demo feature behind `KB_DEMO_LOGIN` (set it to `false` outside a demo). Login, reset, upload and search are rate limited per client.
- **Authorization.** Every read runs as the `NOLOGIN` role `kb_app` under PostgreSQL row-level security, so a missing check in our code still cannot leak a document. Writes check `rbac.py` first: share only with your own groups, replace only your own document (or as a group manager or admin), reset only as admin with the password.
- **Business logic.** Trust signals cannot be faked: marking a document as official `policy` or naming an accountable `owner` is limited to managers of a group it is shared with, and admins. The uploader's position, department and manager flag come from their profile, never from the upload. Notifications, duplicate results and disagreements never name documents the recipient cannot read, and the documents page does not reveal how many hidden documents exist.
- **IDOR.** The acting user always comes from the signed cookie, never from a request body or URL. Notifications can only be marked read by their owner (checked in SQL under RLS). Upload ids are namespaced per user.
- **Injection and browser.** SQL uses bound parameters and `psycopg.sql` composition only. Strict Content-Security-Policy (`script-src 'self'`, no inline scripts), `X-Frame-Options: DENY`, `nosniff`, HSTS and `no-referrer`; API responses are `no-store`; all rendered text is escaped. No public OpenAPI docs. Request sizes are bounded.
- **Deployment.** The container runs as a non-root user with `no-new-privileges` and all capabilities dropped. Postgres and the app bind to localhost. Secrets live in `.env`, which git ignores.
- **Known limits.** `kb.user_id` is a session setting: RLS guards against bugs in our own queries, not against someone who can already run arbitrary SQL. BM25 statistics are corpus-wide, so hidden documents slightly influence scores (they never appear in results). The expert directory is organisation-wide and readable by every signed-in user; it holds work profiles only.

## Unfinished / next

- The login for regular users is a demo user picker. Production needs real SSO (e.g. Entra ID) in front of it, and server-side session revocation.
- Contradiction detection only compares numeric facts, and only within one language.
- The context and expert weights are hand-picked, not tuned on real queries.
- Rate limits are in memory, per process.
- Index views are rebuilt in full on every upload, and duplicate detection compares every document: fine for thousands of chunks, not beyond.
- Text search uses the `english` configuration only; the vector side covers other languages.
- Alerts have a status (`open/dismissed/resolved`), but there is no API yet to change it. Notifications are database rows only, with no email or Teams delivery.
