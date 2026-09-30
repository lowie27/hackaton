-- Near-duplicate detection results and the notifications they produce.

CREATE TABLE IF NOT EXISTS kb.similarity_alerts (
    id               bigserial PRIMARY KEY,
    new_doc_id       bigint NOT NULL REFERENCES kb.documents (id) ON DELETE CASCADE,
    existing_doc_id  bigint NOT NULL REFERENCES kb.documents (id) ON DELETE CASCADE,
    lexical_score    float8,  -- Jaccard overlap of stemmed terms
    vector_score     float8,  -- cosine similarity of document embeddings (NULL when vectors are off)
    status           text   NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'dismissed', 'resolved')),
    created_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (new_doc_id, existing_doc_id),
    CHECK (new_doc_id <> existing_doc_id)
);

CREATE TABLE IF NOT EXISTS kb.notifications (
    id          bigserial PRIMARY KEY,
    user_id     bigint NOT NULL REFERENCES kb.users (id) ON DELETE CASCADE,
    alert_id    bigint REFERENCES kb.similarity_alerts (id) ON DELETE CASCADE,
    message     text   NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    read_at     timestamptz
);

CREATE INDEX IF NOT EXISTS notifications_user ON kb.notifications (user_id, created_at DESC);
