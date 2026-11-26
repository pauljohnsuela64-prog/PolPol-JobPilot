BEGIN;
CREATE TABLE IF NOT EXISTS assistant_handoffs (
    token_hash char(64) PRIMARY KEY,
    listing_id integer NOT NULL REFERENCES discovered_jobs(id) ON DELETE CASCADE,
    revision_key char(64) NOT NULL,
    expires_at timestamptz NOT NULL,
    receipt_hash char(64) UNIQUE,
    authorized_keys text[],
    filled_keys text[]
);
CREATE INDEX IF NOT EXISTS assistant_handoffs_listing ON assistant_handoffs(listing_id);
CREATE TABLE IF NOT EXISTS assistant_activity (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    listing_id integer NOT NULL REFERENCES discovered_jobs(id) ON DELETE CASCADE,
    event text NOT NULL CHECK (event IN ('prepared','copy_requested','fill_authorized','filled')),
    field_count integer NOT NULL CHECK (field_count >= 0),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS assistant_activity_listing ON assistant_activity(listing_id,id DESC);
COMMIT;
