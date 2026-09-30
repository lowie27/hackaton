# hackaton: SD Worx "Unlock the Knowledge Within"

Tectonic Hackathon 2026, SD Worx case. The knowledge layer behind a search UI:

- **Hybrid search.** BM25 is computed inside PostgreSQL, pgvector adds semantic matches, and Python fuses both rankings with reciprocal rank fusion. Vectors can be switched off with one setting. Every hit shows *why* it matched (BM25 rank and terms, vector rank and cosine) plus its owner, country, last update and groups. *"What applies in this context?"*
- **Near-duplicate alerts.** Each upload is compared with all existing documents (term overlap, plus embedding similarity when vectors are on). The uploader, the uploader of the other document and the group managers get a notification. *"Detect: conflicting, duplicated, missing or outdated knowledge."*
- **Context-aware ranking that explains itself.** Documents carry country, site, department, language, tags, a validity period and a snapshot of the uploader's position; users have a profile. Results that apply to the person asking move up, and every hit lists its reasons ("applies to your country (BE)", "official policy document") and warnings ("expired on 2024-12-31", "no accountable owner", "applies to NL, you work in BE"). Hard filters (country, department, source, language, tags, valid on a date) run in SQL before top-k. *"What is current? Which answer should a person trust?"*
- **RBAC.** Users see only documents shared with their groups (or uploaded by them). PostgreSQL row-level security enforces this, so hidden documents never reach a result list, not even as a slot in the top-k. Notifications never name a document the recipient cannot read.

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
                                   ─► RRF in Python ─► SearchHit list
```

| File | What |
|---|---|
| `sql/001_bm25.sql` | chunks, inverted-index views, `kb.bm25_search` (Okapi BM25, Lucene IDF) |
| `sql/002_rbac.sql` | users, groups, memberships (member/manager), document shares |
| `sql/003_alerts.sql` | similarity alerts and notifications |
| `sql/004_security.sql` | `kb_app` role, grants, row-level security policies |
| `sql/optional/vector.sql` | pgvector columns + HNSW indexes (only when vectors are on) |
| `src/kb/service.py` | **`KnowledgeBase`: the API for the UI** |
| `sql/005_metadata.sql` | user profiles and document context metadata |
| `src/kb/retriever.py` | hybrid retrieval, RRF, metadata filters |
| `src/kb/context.py` | context-aware re-ranking with reasons and warnings |
| `src/kb/alerts.py` | duplicate detection, notifications |
| `src/kb/rbac.py` | user/group admin and write permission checks |

## Run

```bash
cp .env.example .env            # change the password in both places
docker compose up -d            # Postgres 17 with pgvector, bound to localhost
python -m venv .venv && . .venv/bin/activate
pip install -e '.[vector,dev]'  # drop "vector," for BM25 only

python -m kb init               # schema + RLS (+ pgvector if KB_VECTOR_ENABLED=true)
python -m kb seed               # demo users, groups, documents (data/sample/seed.json)
python -m kb search "double holiday pay" --as bram@example.com
python -m kb search "holiday pay" --as bram@example.com --valid-on 2026-09-30 --tag "holiday pay"
python -m kb notifications --as anna@example.com
pytest
```

Toggle vectors with `KB_VECTOR_ENABLED=true|false` in `.env`. After turning them on for existing data, run `python -m kb init` and then `python -m kb embed`. The default model (`paraphrase-multilingual-MiniLM-L12-v2`, runs locally via fastembed) is multilingual, so Dutch and French queries match English documents.

### Using it from the UI/API layer

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

- Reads run as the `NOLOGIN` role `kb_app` with RLS. Writes go through the owner connection after explicit checks: you can only share with your own groups, and only the uploader, a group manager or an admin can overwrite a document.
- `kb.user_id` is a session setting. RLS guards against bugs in our own queries, not against someone who can already run arbitrary SQL.
- BM25 statistics (document frequency, average length) are corpus-wide, so hidden documents slightly influence scores. They never appear in results.

Document metadata comes from frontmatter (`country, location, department, language, tags, valid_from, valid_until, source, owner, updated_at`) or the `meta` dict of `Document`. Set `KB_CONTEXT_RANKING=false` for pure relevance ranking.

## Unfinished / next

- Context weights in `src/kb/context.py` are hand-picked, not tuned on real queries.

- Index views are rebuilt in full on every upload. That's fine for thousands of chunks; beyond that, switch to trigger-maintained tables.
- Text search uses the `english` config only. The vector side covers other languages.
- Duplicate detection compares every document's term set: O(corpus) per upload.
- Alerts have a status (`open/dismissed/resolved`), but there is no API yet to change it.
- Notifications are rows in the database only, with no email or Teams delivery.
