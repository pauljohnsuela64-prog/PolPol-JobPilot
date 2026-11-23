BEGIN;

CREATE TABLE IF NOT EXISTS career_boards (
    id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    provider text NOT NULL CHECK (provider IN ('greenhouse', 'lever', 'lever-eu')),
    board text NOT NULL CHECK (board ~ '^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$'),
    company varchar(120) NOT NULL CHECK (length(trim(company)) > 0),
    enabled boolean NOT NULL DEFAULT true,
    UNIQUE (provider, board)
);

CREATE TABLE IF NOT EXISTS discovered_jobs (
    id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id integer NOT NULL REFERENCES career_boards(id),
    provider text NOT NULL CHECK (provider IN ('greenhouse', 'lever', 'lever-eu')),
    external_id varchar(128) NOT NULL,
    posting jsonb NOT NULL CHECK (jsonb_typeof(posting) = 'object'),
    discovered_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    tracked_job_id integer UNIQUE REFERENCES jobs(id) ON DELETE SET NULL,
    UNIQUE (provider, external_id)
);

COMMIT;
