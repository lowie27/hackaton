# hackaton: SD Worx "Unlock the Knowledge Within"

Tectonic Hackathon 2026, SD Worx case. The knowledge layer behind a search UI:

- **Hybrid search.** BM25 is computed inside PostgreSQL, pgvector adds semantic matches, and Python fuses both rankings with reciprocal rank fusion. Vectors can be switched off with one setting. Every hit shows *why* it matched (BM25 rank and terms, vector rank and cosine) plus its owner, country, last update and groups. *"What applies in this context?"*
- **Rerank for accuracy.** After fusion, a cross-encoder (`jinaai/jina-reranker-v2-base-multilingual`, local via fastembed) reads the question together with each of the top 20 candidates and re-scores them. The first pass is fast but judges words or vectors separately; the cross-encoder judges whether the passage actually answers the question. It only sees the shortlist (after RLS), so it adds accuracy without scanning the corpus. Each hit shows its rerank rank and score.
- **Near-duplicate alerts.** Each upload is compared with all existing documents (term overlap, plus embedding similarity when vectors are on). The uploader, the uploader of the other document and the group managers get a notification. *"Detect: conflicting, duplicated, missing or outdated knowledge."*
- **Context-aware ranking that explains itself.** Documents carry country, site, department, language, tags, a validity period and a snapshot of the uploader's position; users have a profile. Results that apply to the person asking move up, and every hit lists its reasons ("applies to your country (BE)", "official policy document") and warnings ("expired on 2024-12-31", "no accountable owner", "applies to NL, you work in BE"). Hard filters (country, department, source, language, tags, valid on a date) run in SQL before top-k. *"What is current? Which answer should a person trust?"*
- **Where results disagree.** Facts (percentages, euro amounts, numbers of days/weeks/months/years) are extracted from every result and compared: same kind of fact, sentences about the same thing, same country (or global). When the values differ, the UI shows a box above the results with every value, which document says it, the quoted sentence, and which one to trust: the claim from the most trustworthy source (context signals), not the best text match. A result that contradicts a more trustworthy one loses 40% with the warning "contradicts a more trustworthy source on the amount (EUR 129 vs EUR 150)". Different countries are never compared (BE 92% vs NL 8% is a different context). Rule-based, no language model: every dispute traces back to two quoted sentences. *"Detect: conflicting knowledge. Which answer should a person trust?"*
- **Ask a person when the documents are not enough.** The whole result list is judged (`src/kb/escalation.py`): no result really answers the question (rerank below 30%), the best result cannot be relied on (expired, stale, no owner), sources disagree without a clear winner, or near-identical copies exist. Then the page recommends people, from an expert directory in a **separate database** (`kb_experts`), ranked the same way as documents (`src/kb/experts.py`): BM25 in SQL on their expertise, meaning, rerank, then context signals with reasons and warnings (same country, department and office, *owns a document in your results*, *owns the most trustworthy version*, out of office, inactive, experience). *"Who has relevant expertise? Connect: find the right expertise when documents are not enough."*
- **RBAC.** Users see only documents shared with their groups (or uploaded by them). PostgreSQL row-level security enforces this, so hidden documents never reach a result list, not even as a slot in the top-k. Notifications never name a document the recipient cannot read.

## How to run

You need Python 3.11+ and Docker. Run everything from the repo root.

### First time

```bash
# 1. Database: Postgres 17 with pgvector, only reachable from localhost
cp .env.example .env            # then change the password in BOTH places in .env
docker compose up -d

# 2. Python environment and dependencies
python -m venv .venv
. .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 3. Create the schema and load the demo data
python -m kb init               # tables, search index, RLS (+ pgvector), and the kb_experts database
python -m kb seed               # demo users, groups and documents from data/sample/
```

### Every time after that

```bash
docker compose up -d            # start the database if it is not running
. .venv/bin/activate
```

### Try the demo

```bash
# Bram (payroll consultant, BE) searches: 2025 policy first, Teams copy and expired 2019 FAQ flagged
python -m kb search "double holiday pay" --as bram@example.com

# Same search, only documents in force today with a given tag
python -m kb search "holiday pay" --as bram@example.com --valid-on 2026-09-30 --tag "holiday pay"

# Duplicate alerts Anna received
python -m kb notifications --as anna@example.com
python -m kb experts "can I still order a hybrid company car" --as bram@example.com

python -m kb --help             # all commands
```

Demo users: `admin@`, `anna@`, `bram@`, `noor@example.com` (see `data/sample/seed.json`).

### Web UI (demo)

```bash
docker compose up -d --build   # database + web UI on http://localhost:8000
```

Pick a demo user, then:

- **Search**: results with *why it matched* (keyword terms, meaning similarity), metadata, and green *why you can rely on it* / amber *be careful* signals.
- **Sidebar**: switch the search logic (Keyword / Meaning / Hybrid), the cross-encoder rerank, context ranking on/off, each context signal on/off, hard filters, and what is displayed (metadata, match reasons, trust signals).
- **Compare logics**: the same query side by side under two configurations (keyword vs hybrid, relevance only vs context-aware, ...) with rank changes marked.
- **Upload**: add a document with metadata, duplicate check on/off. Two example documents that are not in the database (`data/upload_examples/`) fill the form: a unique one (bike leasing) and a near-copy of the home-working allowance policy that triggers a duplicate alert. **What I can see**: the documents RBAC lets you read. **Notifications**: duplicate alerts.
- Admins get a *Reset demo data* button. It also asks for `KB_ADMIN_PASSWORD` (unset = reset disabled), because the demo login itself has no passwords.

The first start downloads the embedding model (about 200 MB, cached in a volume). Set `KB_WEB_VECTOR_ENABLED=false` in `.env` for keyword-only. Behind a reverse proxy on an external `edge` network: `docker compose -f docker-compose.yml -f docker-compose.edge.yml up -d`.

The login is a demo user picker, not real authentication: the selected user id is kept in an HMAC-signed, HttpOnly cookie (`KB_SESSION_SECRET`), and every endpoint takes the user from that cookie, never from the request.

### Run the tests

```bash
pytest                          # 84 tests; needs the database from step 1
```

### Options

`requirements.txt` includes `fastembed` for vector search. For BM25 only (smaller install, no model download) use `pip install -e .` instead and keep `KB_VECTOR_ENABLED=false`.

**Rerank.** Set `KB_RERANK_ENABLED=true` (needs the `vector` extra; the model is about 1.1 GB and downloads on first use). `KB_RERANK_MODEL` picks another fastembed cross-encoder, for example `Xenova/ms-marco-MiniLM-L-6-v2` (80 MB, English only). `KB_RERANK_CANDIDATES` (default 20) sets how many fused results are re-scored. From code: `kb.search(user_id, q, rerank=True/False)` overrides it per request. The web UI turns it on by default (`KB_WEB_RERANK_ENABLED`) and has a *Rerank top 20* switch and a *Without vs with rerank* comparison.

Toggle vectors with `KB_VECTOR_ENABLED=true|false` in `.env`. After turning them on for existing data, run `python -m kb init` and then `python -m kb embed`. The default model (`paraphrase-multilingual-MiniLM-L12-v2`, runs locally via fastembed) is multilingual, so Dutch and French queries match English documents.


## Architecture

```
upload ─► KnowledgeBase.upload (permission checks) ─► kb.documents / kb.chunks (+ embedding)
                     │                                  │ CALL kb.refresh_index()
                     ▼                                  ▼
          alerts.detect_and_notify           kb.chunk_terms / chunk_len / term_df (materialized views)
          (Jaccard + cosine, all docs)
                     │
                     ▼
          kb.similarity_alerts ─► kb.notifications (per recipient, redacted)

search ─► KnowledgeBase.search ─► user_session: SET LOCAL ROLE kb_app + kb.user_id
                                   ├─ kb.bm25_search(q)          (SQL, RLS-filtered)
                                   └─ chunks ORDER BY embedding <=> q  (pgvector, RLS-filtered)
                                   ─► RRF in Python
                                   ─► cross-encoder rerank of the top 20 (optional)
                                   ─► context ranking (kb.context)
                                   ─► where results disagree (kb.conflicts), demote contradicted ─► SearchHit list
```

| File | What |
|---|---|
| `sql/001_bm25.sql` | chunks, inverted-index views, `kb.bm25_search` (Okapi BM25, Lucene IDF) |
| `sql/002_rbac.sql` | users, groups, memberships (member/manager), document shares |
| `sql/003_alerts.sql` | similarity alerts and notifications |
| `sql/004_security.sql` | `kb_app` role, grants, row-level security policies |
| `sql/optional/vector.sql` | pgvector columns + HNSW indexes (only when vectors are on) |
| `src/kb/service.py` | **`KnowledgeBase`: the API for the UI** |
| `src/kb/web/` | FastAPI JSON API + single-page demo UI |
| `sql/005_metadata.sql` | user profiles and document context metadata |
| `src/kb/retriever.py` | hybrid retrieval, RRF, rerank, metadata filters |
| `src/kb/context.py` | context-aware re-ranking with reasons and warnings |
| `src/kb/conflicts.py` | finds where results disagree, and which claim to trust |
| `src/kb/escalation.py` | decides when a person should answer instead of the documents |
| `src/kb/experts.py`, `sql/experts/` | expert directory in its own database, ranked like documents |
| `src/kb/alerts.py` | duplicate detection, notifications |
| `src/kb/rbac.py` | user/group admin and write permission checks |

## Using it from the UI/API layer

```python
from kb import KnowledgeBase, SearchFilters
from kb.db import connect
from kb.ingest import Document

kb = KnowledgeBase(connect())                     # settings from .env
hits = kb.search(user_id, "double holiday pay")   # list[SearchHit], with .reasons and .warnings
hits = kb.search(user_id, "holiday pay", filters=SearchFilters(country="BE", valid_on=date.today()))
result = kb.upload(user_id, Document("hr/leave.md", "Leave policy", text, {"country": "BE"}), ["payroll-be"])
result.similar                                    # near-duplicates the uploader may see
kb.notifications(user_id); kb.mark_notification_read(user_id, notification_id)
```

The API layer must authenticate the user and pass *their* id. It must never take a user id from the request body. `KnowledgeBase` raises `rbac.PermissionDenied` for forbidden writes.

## Security notes

What the Aikido AI Code Audit looks for, and how this code handles it (tests in `tests/test_rbac.py`, `tests/test_web_security.py`):

- **Authentication.** Session = user id + expiry, HMAC-SHA256 signed (`KB_SESSION_SECRET`), in an HttpOnly, Secure, SameSite=Strict cookie that expires after 8 hours; forged or expired cookies get 401. Admin accounts can never log in without `KB_ADMIN_PASSWORD` (constant-time compare); unset = no admin login and no reset. The passwordless picker for non-admin demo users is a demo feature behind `KB_DEMO_LOGIN` (set `false` outside a demo; production needs SSO). Login, reset, upload and search are rate limited per client.
- **Authorization.** Every read runs as the `kb_app` role under PostgreSQL row-level security, so a missing check in our code still cannot leak a document. Writes check `rbac.py` first: share only with your own groups, replace only your own document (or as group manager/admin), reset only as admin with the password.
- **Business logic.** Trust signals cannot be faked: marking a document as official `policy` or naming an accountable `owner` is limited to managers of a group it is shared with (and admins); the uploader's position, department and manager flag come from their profile, never from the upload. Notifications, duplicate results and disagreements never name documents the recipient cannot read, and the documents page does not reveal how many hidden documents exist.
- **IDOR.** The acting user always comes from the signed cookie, never from a request body or URL; notifications can only be marked read by their owner (checked in SQL under RLS); upload ids are namespaced per user.
- **Injection and browser.** SQL uses bound parameters and `psycopg.sql` composition only. Strict Content-Security-Policy (`script-src 'self'`, no inline scripts), `X-Frame-Options: DENY`, `nosniff`, HSTS, `no-referrer`; API responses are `no-store`; all rendered text is escaped. No public OpenAPI docs. Request sizes are bounded.
- **Deployment.** The container runs as a non-root user with `no-new-privileges` and all capabilities dropped; Postgres and the app bind to localhost; secrets live in `.env` (git-ignored).

- Reads run as the `NOLOGIN` role `kb_app` with RLS. Writes go through the owner connection after explicit checks: you can only share with your own groups, and only the uploader, a group manager or an admin can overwrite a document.
- `kb.user_id` is a session setting. RLS guards against bugs in our own queries, not against someone who can already run arbitrary SQL.
- BM25 statistics (document frequency, average length) are corpus-wide, so hidden documents slightly influence scores. They never appear in results.

Document metadata comes from frontmatter (`country, location, department, language, tags, valid_from, valid_until, source, owner, updated_at`) or the `meta` dict of `Document`. Set `KB_CONTEXT_RANKING=false` for pure relevance ranking.

## Unfinished / next

- The web login is a demo user picker. Production needs real SSO (e.g. Entra ID) in front of it.

- Context weights in `src/kb/context.py` are hand-picked, not tuned on real queries.

- Index views are rebuilt in full on every upload. That's fine for thousands of chunks; beyond that, switch to trigger-maintained tables.
- Text search uses the `english` config only. The vector side covers other languages.
- Duplicate detection compares every document's term set: O(corpus) per upload.
- Alerts have a status (`open/dismissed/resolved`), but there is no API yet to change it.
- Notifications are rows in the database only, with no email or Teams delivery.
