CREATE TABLE IF NOT EXISTS ai_settings (
    id integer PRIMARY KEY CHECK (id = 1),
    enabled boolean NOT NULL DEFAULT false,
    endpoint text NOT NULL DEFAULT 'http://127.0.0.1:11434',
    model text NOT NULL DEFAULT ''
);
