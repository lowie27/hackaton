# hackaton: SD Worx "Unlock the Knowledge Within"

Tectonic Hackathon 2026, SD Worx case. Retrieval layer for organisational knowledge:
**PostgreSQL computes BM25**, **Python does the retrieval** and returns every hit with
its trust metadata (owner, country, last update, matched terms).

## Architecture

```
 .md/.txt files ──► python -m kb ingest ──► kb.documents / kb.chunks (tsvector per chunk)
                                                 │  CALL kb.refresh_index()
                                                 ▼
                          materialized views: kb.chunk_terms (inverted index)
                                              kb.chunk_len, kb.term_df
                          view:               kb.corpus_stats (N, avgdl)
                                                 │
 BM25Retriever.search() ──► SELECT … FROM kb.bm25_search(q, k, k1, b) ⋈ chunks ⋈ documents
```

- `sql/001_bm25.sql`: the schema, the index views and the `kb.bm25_search` table function (Okapi BM25, Lucene IDF).
- `src/kb/ingest.py`: frontmatter parsing, paragraph chunking (~200 words), upsert.
- `src/kb/retriever.py`: `BM25Retriever`, which returns `SearchHit` dataclasses.
- `tests/test_bm25_sql.py`: checks the SQL scores against a plain-Python BM25.

## Run

```bash
cp .env.example .env            # then change the password in both places
docker compose up -d
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'

python -m kb init
python -m kb ingest data/sample
python -m kb search "double holiday pay Belgium"
pytest
```

Documents can carry frontmatter (`title`, `source`, `owner`, `country`, `updated_at`).

## Unfinished / next

- The index views are rebuilt in full on every ingest. That is fine for thousands of chunks; beyond that, switch to trigger-maintained tables.
- Text search uses the `english` config only. Dutch and French documents need a per-document config.
- No trust ranking, conflict detection or LLM answer layer yet.
