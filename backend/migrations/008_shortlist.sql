BEGIN;

CREATE TABLE IF NOT EXISTS shortlist_settings (
    id integer PRIMARY KEY CHECK (id = 1),
    min_score integer NOT NULL DEFAULT 70 CHECK (min_score BETWEEN 0 AND 100),
    min_confidence integer NOT NULL DEFAULT 0 CHECK (min_confidence BETWEEN 0 AND 100)
);
INSERT INTO shortlist_settings (id) VALUES (1) ON CONFLICT DO NOTHING;

ALTER TABLE discovered_jobs ADD COLUMN IF NOT EXISTS reviewed boolean NOT NULL DEFAULT false;

CREATE TABLE IF NOT EXISTS shortlist_activity (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind text NOT NULL CHECK (kind IN ('new_match','refresh_failed')),
    listing_id integer UNIQUE REFERENCES discovered_jobs(id) ON DELETE CASCADE,
    source_id integer REFERENCES career_boards(id) ON DELETE SET NULL,
    title text,
    company text NOT NULL,
    detail text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
COMMIT;
