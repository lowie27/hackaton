-- Users, groups and which groups a document is shared with.
-- Access rule (see kb.readable_document_ids in 004_security.sql):
--   admins read everything; others read documents they uploaded or that are
--   shared with one of their groups. A document without groups is private.

CREATE TABLE IF NOT EXISTS kb.users (
    id            bigserial PRIMARY KEY,
    email         text    NOT NULL UNIQUE,
    display_name  text    NOT NULL,
    is_admin      boolean NOT NULL DEFAULT false
);

CREATE TABLE IF NOT EXISTS kb.groups (
    id    bigserial PRIMARY KEY,
    name  text NOT NULL UNIQUE
);

-- Managers are the "people in power" for a group: they review duplicate alerts.
CREATE TABLE IF NOT EXISTS kb.group_members (
    group_id  bigint NOT NULL REFERENCES kb.groups (id) ON DELETE CASCADE,
    user_id   bigint NOT NULL REFERENCES kb.users (id) ON DELETE CASCADE,
    role      text   NOT NULL DEFAULT 'member' CHECK (role IN ('member', 'manager')),
    PRIMARY KEY (group_id, user_id)
);

CREATE INDEX IF NOT EXISTS group_members_user ON kb.group_members (user_id);

CREATE TABLE IF NOT EXISTS kb.document_groups (
    doc_id    bigint NOT NULL REFERENCES kb.documents (id) ON DELETE CASCADE,
    group_id  bigint NOT NULL REFERENCES kb.groups (id) ON DELETE CASCADE,
    PRIMARY KEY (doc_id, group_id)
);

CREATE INDEX IF NOT EXISTS document_groups_group ON kb.document_groups (group_id);

ALTER TABLE kb.documents
    ADD COLUMN IF NOT EXISTS uploaded_by bigint REFERENCES kb.users (id) ON DELETE SET NULL;
