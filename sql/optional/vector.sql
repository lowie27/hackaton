-- pgvector half of hybrid search. Applied by `python -m kb init` only when
-- KB_VECTOR_ENABLED is on. {{EMBEDDING_DIM}} is filled in from KB_EMBEDDING_DIM;
-- changing the model's dimension later needs these columns dropped first.

-- Pinned to public: with a DB user named "kb", "$user" in search_path would
-- otherwise put the type in schema kb, where kb_app does not look for it.
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;

ALTER TABLE kb.chunks    ADD COLUMN IF NOT EXISTS embedding vector({{EMBEDDING_DIM}});
-- Mean of the document's chunk embeddings, used for near-duplicate detection.
ALTER TABLE kb.documents ADD COLUMN IF NOT EXISTS embedding vector({{EMBEDDING_DIM}});

CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw
    ON kb.chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS documents_embedding_hnsw
    ON kb.documents USING hnsw (embedding vector_cosine_ops);
