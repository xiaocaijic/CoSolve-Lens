CREATE TABLE studychat_sample_evidence_review (
    job_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    finding TEXT NOT NULL CHECK(finding='out_of_task'),
    PRIMARY KEY(job_id, session_id, event_id),
    FOREIGN KEY (job_id, session_id) REFERENCES studychat_sample_members(job_id, session_id) ON DELETE CASCADE
);
