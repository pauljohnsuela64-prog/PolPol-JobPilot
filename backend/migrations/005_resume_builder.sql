BEGIN;
CREATE TABLE IF NOT EXISTS master_resume (
    id integer PRIMARY KEY CHECK (id = 1),
    facts jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS resume_drafts (
    id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    listing_id integer NOT NULL REFERENCES discovered_jobs(id),
    source_snapshot jsonb NOT NULL,
    content text NOT NULL,
    revision integer NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS resume_drafts_listing ON resume_drafts(listing_id, id DESC);
COMMIT;
