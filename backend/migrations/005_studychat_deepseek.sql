CREATE TABLE studychat_deepseek_consents (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES data_sources(id) ON DELETE CASCADE,
    approver_id TEXT NOT NULL REFERENCES users(id),
    enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
    scope_version TEXT NOT NULL,
    purpose TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX studychat_deepseek_consents_source ON studychat_deepseek_consents(source_id);
