BEGIN;

CREATE TABLE IF NOT EXISTS discovery_schedule (
    id integer PRIMARY KEY CHECK (id = 1),
    enabled boolean NOT NULL DEFAULT false,
    interval_minutes integer NOT NULL DEFAULT 60 CHECK (interval_minutes BETWEEN 15 AND 10080),
    next_run_at timestamptz,
    last_run_at timestamptz,
    last_error text
);
INSERT INTO discovery_schedule (id) VALUES (1) ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS discovery_provider_cooldowns (
    host text PRIMARY KEY,
    retry_at timestamptz NOT NULL
);

COMMIT;
