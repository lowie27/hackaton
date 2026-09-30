-- BM25 search implemented inside PostgreSQL.
--
-- Postgres has no SQLite-style "virtual tables", so the index is built from:
--   * base tables          kb.documents, kb.chunks (tsvector generated per chunk)
--   * materialized views   kb.chunk_terms (inverted index), kb.chunk_len, kb.term_df
--   * a view               kb.corpus_stats (N, avgdl)
--   * a table function     kb.bm25_search(query, top_k, k1, b) -> behaves like a table
--
-- After inserting/updating chunks, run: CALL kb.refresh_index();
-- The script is idempotent and safe to re-run.

CREATE SCHEMA IF NOT EXISTS kb;

CREATE TABLE IF NOT EXISTS kb.documents (
    id           bigserial PRIMARY KEY,
    external_id  text        NOT NULL UNIQUE,  -- file path, URL or source-system id
    title        text        NOT NULL,
    source       text,                         -- e.g. "policy", "teams", "wiki"
    owner        text,                         -- who is accountable for this doc
    country      text,                         -- ISO code the doc applies to, NULL = global
    updated_at   timestamptz,                  -- last content update in the source system
    ingested_at  timestamptz NOT NULL DEFAULT now()
);

-- BM25 is computed per chunk, not per document: better granularity for an
-- assistant, and it avoids tsvector's 16383-position limit on long documents.
CREATE TABLE IF NOT EXISTS kb.chunks (
    id       bigserial PRIMARY KEY,
    doc_id   bigint NOT NULL REFERENCES kb.documents (id) ON DELETE CASCADE,
    ord      int    NOT NULL,                  -- position of the chunk in the document
    heading  text   NOT NULL DEFAULT '',       -- document title, indexed with the chunk
    text     text   NOT NULL,
    tsv      tsvector GENERATED ALWAYS AS (
                 to_tsvector('english', heading || ' ' || text)
             ) STORED,
    UNIQUE (doc_id, ord)
);

-- Inverted index: one row per (term, chunk) with the term frequency.
CREATE MATERIALIZED VIEW IF NOT EXISTS kb.chunk_terms AS
SELECT c.id                          AS chunk_id,
       t.lexeme                      AS term,
       cardinality(t.positions)::int AS tf
FROM kb.chunks c,
     unnest(c.tsv) AS t (lexeme, positions, weights);

CREATE UNIQUE INDEX IF NOT EXISTS chunk_terms_term_chunk ON kb.chunk_terms (term, chunk_id);

-- Document length |D| in indexed terms (stopwords excluded).
CREATE MATERIALIZED VIEW IF NOT EXISTS kb.chunk_len AS
SELECT c.id                         AS chunk_id,
       coalesce(sum(ct.tf), 0)::int AS len
FROM kb.chunks c
LEFT JOIN kb.chunk_terms ct ON ct.chunk_id = c.id
GROUP BY c.id;

CREATE UNIQUE INDEX IF NOT EXISTS chunk_len_chunk ON kb.chunk_len (chunk_id);

-- Document frequency per term.
CREATE MATERIALIZED VIEW IF NOT EXISTS kb.term_df AS
SELECT term, count(*)::int AS df
FROM kb.chunk_terms
GROUP BY term;

CREATE UNIQUE INDEX IF NOT EXISTS term_df_term ON kb.term_df (term);

CREATE OR REPLACE VIEW kb.corpus_stats AS
SELECT count(*)::int                  AS n_chunks,
       coalesce(avg(len), 0)::float8  AS avg_len
FROM kb.chunk_len;

CREATE OR REPLACE PROCEDURE kb.refresh_index()
LANGUAGE plpgsql AS $$
BEGIN
    -- Order matters: chunk_len and term_df are derived from chunk_terms.
    REFRESH MATERIALIZED VIEW kb.chunk_terms;
    REFRESH MATERIALIZED VIEW kb.chunk_len;
    REFRESH MATERIALIZED VIEW kb.term_df;
END;
$$;

-- Okapi BM25 with Lucene's non-negative IDF:
--   idf(t)      = ln(1 + (N - df + 0.5) / (df + 0.5))
--   score(D, Q) = sum_t idf(t) * tf * (k1 + 1) / (tf + k1 * (1 - b + b * |D| / avgdl))
-- The query goes through the same to_tsvector config as the chunks, so
-- stemming and stopwords match on both sides.
CREATE OR REPLACE FUNCTION kb.bm25_search(
    q     text,
    top_k int    DEFAULT 10,
    k1    float8 DEFAULT 1.2,
    b     float8 DEFAULT 0.75
)
RETURNS TABLE (chunk_id bigint, score float8, matched_terms text[])
LANGUAGE sql STABLE AS $$
    WITH qterms AS (
        SELECT DISTINCT t.lexeme AS term
        FROM unnest(to_tsvector('english', q)) AS t (lexeme, positions, weights)
    )
    SELECT ct.chunk_id,
           sum(
               ln(1 + (s.n_chunks - df.df + 0.5) / (df.df + 0.5))
               * ct.tf * (k1 + 1)
               / (ct.tf + k1 * (1 - b + b * cl.len / s.avg_len))
           ) AS score,
           array_agg(ct.term ORDER BY ct.term) AS matched_terms
    FROM qterms qt
    JOIN kb.chunk_terms ct ON ct.term = qt.term
    JOIN kb.term_df df     ON df.term = qt.term
    JOIN kb.chunk_len cl   ON cl.chunk_id = ct.chunk_id
    -- Row-level security on kb.chunks (004_security.sql) filters here, before
    -- LIMIT, so hidden chunks never take a slot in the top_k.
    JOIN kb.chunks c       ON c.id = ct.chunk_id
    CROSS JOIN kb.corpus_stats s
    GROUP BY ct.chunk_id
    ORDER BY score DESC, ct.chunk_id
    LIMIT top_k;
$$;
