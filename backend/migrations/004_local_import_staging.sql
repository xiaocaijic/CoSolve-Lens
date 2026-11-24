CREATE TABLE import_batch_records (
    batch_id TEXT NOT NULL REFERENCES import_batches(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    body TEXT NOT NULL,
    PRIMARY KEY(batch_id, ordinal)
);
