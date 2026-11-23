CREATE TABLE IF NOT EXISTS jobs (
    id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    title varchar(120) NOT NULL CHECK (length(trim(title)) > 0),
    company varchar(120) NOT NULL CHECK (length(trim(company)) > 0),
    location varchar(120) NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS applications (
    job_id integer PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
    status text NOT NULL DEFAULT 'saved'
        CHECK (status IN ('saved', 'applied', 'interview', 'offer', 'closed'))
);
