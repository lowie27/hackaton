-- Expert directory, in its own database (kb_experts). Applied by `python -m kb init`.
-- Ranked like documents: BM25 in SQL, then meaning, rerank and context in Python (kb.experts).

CREATE SCHEMA IF NOT EXISTS experts;

CREATE TABLE IF NOT EXISTS experts.people (
    id           bigserial PRIMARY KEY,
    email        text    NOT NULL UNIQUE,
    name         text    NOT NULL,
    position     text,
    department   text,
    country      text,                         -- ISO code, NULL = group-wide role
    location     text,
    languages    text[]  NOT NULL DEFAULT '{}',
    expertise    text    NOT NULL,             -- free text: what to ask this person about
    tags         text[]  NOT NULL DEFAULT '{}',
    owns         text[]  NOT NULL DEFAULT '{}', -- document owners (kb.documents.owner) this person answers for
    away_until   date,                         -- out of office until (inclusive)
    last_active  date,
    answered     int     NOT NULL DEFAULT 0,   -- questions answered through the knowledge base
    search_text  text    NOT NULL DEFAULT '',  -- name, position, expertise and tags, filled on write
    embedding    real[],                       -- same model as the documents; cosine in Python
    tsv          tsvector GENERATED ALWAYS AS (to_tsvector('english', search_text)) STORED
);

-- Okapi BM25 over the directory, same formula as kb.bm25_search. Computed on the
-- fly: the directory is small, so no materialized index is needed.
CREATE OR REPLACE FUNCTION experts.bm25_search(
    q     text,
    top_k int    DEFAULT 20,
    k1    float8 DEFAULT 1.2,
    b     float8 DEFAULT 0.75
)
RETURNS TABLE (person_id bigint, score float8, matched_terms text[])
LANGUAGE sql STABLE AS $$
    WITH terms AS (
        SELECT p.id, t.lexeme AS term, cardinality(t.positions)::int AS tf
        FROM experts.people p, unnest(p.tsv) AS t (lexeme, positions, weights)
    ),
    lens  AS (SELECT id, sum(tf)::float8 AS len FROM terms GROUP BY id),
    stats AS (SELECT count(*)::float8 AS n, coalesce(avg(len), 0) AS avg_len FROM lens),
    df    AS (SELECT term, count(*)::float8 AS df FROM terms GROUP BY term),
    qterms AS (SELECT DISTINCT t.lexeme AS term FROM unnest(to_tsvector('english', q)) AS t (lexeme, positions, weights))
    SELECT t.id,
           sum(ln(1 + (s.n - df.df + 0.5) / (df.df + 0.5))
               * t.tf * (k1 + 1) / (t.tf + k1 * (1 - b + b * l.len / s.avg_len))) AS score,
           array_agg(t.term ORDER BY t.term)
    FROM qterms q
    JOIN terms t USING (term)
    JOIN df USING (term)
    JOIN lens l ON l.id = t.id
    CROSS JOIN stats s
    GROUP BY t.id
    ORDER BY score DESC, t.id
    LIMIT top_k;
$$;
