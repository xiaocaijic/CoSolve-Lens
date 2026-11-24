CREATE TABLE studychat_sample_members (
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    model TEXT NOT NULL CHECK(model='deepseek-flash'),
    scope_version TEXT NOT NULL,
    authorization_note TEXT NOT NULL,
    submission_summary TEXT NOT NULL,
    verified_dialogue_messages INTEGER NOT NULL CHECK(verified_dialogue_messages>=1),
    created_at TEXT NOT NULL,
    PRIMARY KEY(job_id, session_id)
);
