-- Row-level security for everything done on behalf of an end user.
--
-- The app connects as the schema owner for trusted work (ingest, index
-- refresh, duplicate detection) and switches to the NOLOGIN role kb_app for
-- user requests, with the user id in the kb.user_id setting (see
-- kb.db.user_session). Postgres then filters documents, chunks and
-- notifications itself, so a query that forgets a permission check still
-- cannot leak rows.
--
-- kb.user_id is a session setting: this guards against bugs in our own
-- queries, not against an attacker who can already run arbitrary SQL.

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'kb_app') THEN
        CREATE ROLE kb_app NOLOGIN;
    END IF;
END
$$;

-- Lets a non-superuser schema owner SET ROLE kb_app.
GRANT kb_app TO CURRENT_USER;

CREATE OR REPLACE FUNCTION kb.current_user_id()
RETURNS bigint
LANGUAGE sql STABLE AS $$
    SELECT nullif(current_setting('kb.user_id', true), '')::bigint
$$;

-- SECURITY DEFINER so it can read users/memberships (not granted to kb_app)
-- and does not recurse into the documents policy.
CREATE OR REPLACE FUNCTION kb.readable_document_ids(uid bigint)
RETURNS SETOF bigint
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
    SELECT d.id
    FROM kb.documents d
    WHERE EXISTS (SELECT 1 FROM kb.users u WHERE u.id = uid AND u.is_admin)
       OR d.uploaded_by = uid
       OR EXISTS (
           SELECT 1
           FROM kb.document_groups dg
           JOIN kb.group_members gm ON gm.group_id = dg.group_id
           WHERE dg.doc_id = d.id AND gm.user_id = uid
       )
$$;

-- The only variant kb_app may call: always scoped to the session's user.
CREATE OR REPLACE FUNCTION kb.my_document_ids()
RETURNS SETOF bigint
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
    SELECT kb.readable_document_ids(kb.current_user_id())
$$;

REVOKE ALL ON FUNCTION kb.readable_document_ids(bigint) FROM PUBLIC;
REVOKE ALL ON FUNCTION kb.my_document_ids() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION kb.my_document_ids() TO kb_app;

GRANT USAGE ON SCHEMA kb TO kb_app;
GRANT SELECT ON kb.documents, kb.chunks, kb.document_groups, kb.groups,
                kb.chunk_terms, kb.chunk_len, kb.term_df, kb.corpus_stats,
                kb.similarity_alerts, kb.notifications
    TO kb_app;
GRANT UPDATE (read_at) ON kb.notifications TO kb_app;

ALTER TABLE kb.documents ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS documents_read ON kb.documents;
CREATE POLICY documents_read ON kb.documents FOR SELECT TO kb_app
    USING (id IN (SELECT kb.my_document_ids()));

ALTER TABLE kb.chunks ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS chunks_read ON kb.chunks;
CREATE POLICY chunks_read ON kb.chunks FOR SELECT TO kb_app
    USING (doc_id IN (SELECT kb.my_document_ids()));

ALTER TABLE kb.document_groups ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS document_groups_read ON kb.document_groups;
CREATE POLICY document_groups_read ON kb.document_groups FOR SELECT TO kb_app
    USING (doc_id IN (SELECT kb.my_document_ids()));

ALTER TABLE kb.notifications ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS notifications_own ON kb.notifications;
CREATE POLICY notifications_own ON kb.notifications FOR ALL TO kb_app
    USING (user_id = kb.current_user_id());

ALTER TABLE kb.similarity_alerts ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS similarity_alerts_notified ON kb.similarity_alerts;
CREATE POLICY similarity_alerts_notified ON kb.similarity_alerts FOR SELECT TO kb_app
    USING (id IN (SELECT n.alert_id FROM kb.notifications n WHERE n.user_id = kb.current_user_id()));
