BEGIN;

CREATE TABLE IF NOT EXISTS profile (
    id integer PRIMARY KEY CHECK (id = 1),
    preferences jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(preferences) = 'object'),
    resume_key uuid,
    resume_filename text,
    resume_uploaded_at timestamptz,
    CHECK (num_nonnulls(resume_key, resume_filename, resume_uploaded_at) IN (0, 3))
);

INSERT INTO profile (id) VALUES (1) ON CONFLICT (id) DO NOTHING;

COMMIT;
