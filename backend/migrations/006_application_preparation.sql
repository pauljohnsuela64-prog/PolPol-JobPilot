BEGIN;

CREATE TABLE IF NOT EXISTS preparation_drafts (
    id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_id integer NOT NULL REFERENCES discovered_jobs(id) ON DELETE CASCADE,
    job_snapshot jsonb NOT NULL,
    resume_snapshot jsonb,
    content jsonb NOT NULL,
    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    approved_revision integer CHECK (approved_revision = revision),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS preparation_drafts_job ON preparation_drafts(job_id, id DESC);

COMMIT;
