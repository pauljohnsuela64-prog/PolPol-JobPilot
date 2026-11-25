BEGIN;
CREATE TABLE IF NOT EXISTS ai_suggestions (
    id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    resume_id integer REFERENCES resume_drafts(id) ON DELETE CASCADE,
    preparation_id integer REFERENCES preparation_drafts(id) ON DELETE CASCADE,
    revision integer NOT NULL CHECK (revision > 0),
    original jsonb,
    proposed jsonb,
    fact_ids jsonb NOT NULL,
    preview_digest text NOT NULL,
    master_digest text NOT NULL,
    applied_revision integer CHECK(applied_revision = revision + 1),
    CHECK (num_nonnulls(resume_id, preparation_id) = 1),
    CHECK (num_nonnulls(original, proposed, applied_revision) IN (0,3))
);
COMMIT;
