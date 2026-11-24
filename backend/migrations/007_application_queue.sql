BEGIN;

ALTER TABLE resume_drafts ADD COLUMN IF NOT EXISTS approved_revision integer
    CHECK (approved_revision > 0 AND approved_revision <= revision);

CREATE TABLE IF NOT EXISTS application_queue_history (
    id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_id integer NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    event text NOT NULL CHECK (event IN ('submitted', 'corrected')),
    created_at timestamptz NOT NULL DEFAULT now(),
    submitted_at timestamptz NOT NULL,
    channel varchar(80) NOT NULL,
    reference varchar(240) NOT NULL DEFAULT '',
    note varchar(2000) NOT NULL DEFAULT '',
    follow_up_on date,
    url text
);
CREATE UNIQUE INDEX IF NOT EXISTS queue_one_submission ON application_queue_history(job_id)
    WHERE event = 'submitted';
CREATE INDEX IF NOT EXISTS queue_history_job ON application_queue_history(job_id, id DESC);

COMMIT;
