ALTER TABLE data_sources ADD COLUMN allow_external_model INTEGER NOT NULL DEFAULT 0;
CREATE TABLE provider_settings (
    id TEXT PRIMARY KEY CHECK(id='active'),
    body TEXT NOT NULL
);
CREATE TABLE provider_calls (
    id TEXT PRIMARY KEY, run_id TEXT NOT NULL UNIQUE REFERENCES evaluation_runs(id) ON DELETE CASCADE,
    snapshot TEXT NOT NULL, created_at TEXT NOT NULL
);
