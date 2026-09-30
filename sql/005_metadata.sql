-- Context metadata for "What applies in this context?".
--
-- Users get a profile (country, location, department, position). Documents get
-- the scope they apply to (department, location, language, tags), a validity
-- period, and a snapshot of who uploaded them at the time. The retriever uses
-- these for filters and for the explained, context-aware ranking in
-- src/kb/context.py.

ALTER TABLE kb.users
    ADD COLUMN IF NOT EXISTS country     text,  -- ISO code the user works in
    ADD COLUMN IF NOT EXISTS location    text,  -- office or site, e.g. "Antwerp"
    ADD COLUMN IF NOT EXISTS department  text,  -- e.g. "Payroll"
    ADD COLUMN IF NOT EXISTS position    text;  -- job title, e.g. "Payroll consultant"

ALTER TABLE kb.documents
    ADD COLUMN IF NOT EXISTS department           text,    -- NULL = all departments
    ADD COLUMN IF NOT EXISTS location             text,    -- NULL = the whole country
    ADD COLUMN IF NOT EXISTS language             text,    -- ISO 639-1, e.g. "en", "nl"
    ADD COLUMN IF NOT EXISTS tags                 text[] NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS valid_from           date,    -- NULL = no start
    ADD COLUMN IF NOT EXISTS valid_until          date,    -- NULL = no end (still in force)
    -- Snapshot at upload time, so a later job change does not rewrite history.
    ADD COLUMN IF NOT EXISTS uploader_position    text,
    ADD COLUMN IF NOT EXISTS uploader_department  text,
    ADD COLUMN IF NOT EXISTS uploader_is_manager  boolean NOT NULL DEFAULT false;

CREATE INDEX IF NOT EXISTS documents_country    ON kb.documents (country);
CREATE INDEX IF NOT EXISTS documents_department ON kb.documents (department);
CREATE INDEX IF NOT EXISTS documents_tags       ON kb.documents USING gin (tags);
