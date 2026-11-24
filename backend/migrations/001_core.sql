CREATE TABLE users (
    id TEXT PRIMARY KEY, role TEXT NOT NULL CHECK(role IN ('student','teacher','admin')),
    display_name TEXT NOT NULL, demo INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE auth_sessions (
    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    csrf TEXT NOT NULL, expires_at REAL NOT NULL
);
CREATE TABLE courses (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, owner_user_id TEXT NOT NULL REFERENCES users(id)
);
CREATE TABLE course_memberships (
    course_id TEXT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id), role TEXT NOT NULL, status TEXT NOT NULL,
    PRIMARY KEY(course_id, user_id)
);
CREATE TABLE data_sources (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, adapter TEXT NOT NULL,
    owner_user_id TEXT NOT NULL REFERENCES users(id), source_identity TEXT NOT NULL,
    source_version TEXT NOT NULL, completeness TEXT NOT NULL, license_note TEXT NOT NULL,
    intended_use TEXT NOT NULL, retain_until TEXT NOT NULL, demo INTEGER NOT NULL,
    created_at TEXT NOT NULL, UNIQUE(owner_user_id, source_identity, source_version)
);
CREATE TABLE data_source_grants (
    id TEXT PRIMARY KEY, data_source_id TEXT NOT NULL REFERENCES data_sources(id) ON DELETE CASCADE,
    user_id TEXT REFERENCES users(id), course_id TEXT REFERENCES courses(id),
    permission TEXT NOT NULL CHECK(permission IN ('view','evaluate','review','export','manage')),
    CHECK((user_id IS NULL) <> (course_id IS NULL))
);
CREATE TABLE participants (
    id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES data_sources(id) ON DELETE CASCADE,
    external_key_hash TEXT NOT NULL, key_version INTEGER NOT NULL DEFAULT 1,
    UNIQUE(source_id, external_key_hash)
);
CREATE TABLE task_definitions (
    id TEXT PRIMARY KEY, version TEXT NOT NULL, title TEXT NOT NULL, instructions TEXT NOT NULL,
    rubric TEXT NOT NULL, tests TEXT NOT NULL, metadata TEXT NOT NULL, status TEXT NOT NULL,
    course_id TEXT REFERENCES courses(id), owner_user_id TEXT REFERENCES users(id),
    source_snapshot_key TEXT UNIQUE
);
CREATE TABLE sessions (
    id TEXT PRIMARY KEY, source_type TEXT NOT NULL CHECK(source_type IN ('dataset','live')),
    source_id TEXT REFERENCES data_sources(id) ON DELETE CASCADE,
    participant_id TEXT REFERENCES participants(id) ON DELETE CASCADE,
    owner_user_id TEXT REFERENCES users(id), course_id TEXT REFERENCES courses(id),
    task_id TEXT NOT NULL REFERENCES task_definitions(id), status TEXT NOT NULL,
    external_key_hash TEXT, metadata TEXT NOT NULL, created_at TEXT NOT NULL,
    UNIQUE(source_id, external_key_hash)
);
CREATE TABLE artifacts (
    id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    kind TEXT NOT NULL, version INTEGER NOT NULL, checksum TEXT NOT NULL,
    content TEXT NOT NULL, mime_type TEXT NOT NULL, UNIQUE(session_id, kind, version)
);
CREATE TABLE events (
    id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    sequence_no INTEGER NOT NULL, event_key TEXT NOT NULL, type TEXT NOT NULL,
    occurred_at TEXT, timestamp_precision TEXT NOT NULL, payload TEXT NOT NULL,
    UNIQUE(session_id, sequence_no), UNIQUE(session_id, event_key)
);
CREATE TABLE submissions (
    id TEXT PRIMARY KEY, session_id TEXT NOT NULL UNIQUE REFERENCES sessions(id) ON DELETE CASCADE,
    artifact_id TEXT NOT NULL REFERENCES artifacts(id), client_event_id TEXT NOT NULL,
    request_hash TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE jobs (
    id TEXT PRIMARY KEY, owner_user_id TEXT NOT NULL REFERENCES users(id), kind TEXT NOT NULL,
    status TEXT NOT NULL, source_id TEXT REFERENCES data_sources(id) ON DELETE CASCADE,
    session_id TEXT REFERENCES sessions(id) ON DELETE CASCADE,
    input_hash TEXT NOT NULL, progress_total INTEGER NOT NULL, progress_done INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0, succeeded_count INTEGER NOT NULL DEFAULT 0,
    skipped_count INTEGER NOT NULL DEFAULT 0, retry_count INTEGER NOT NULL DEFAULT 0,
    result TEXT, error TEXT, lease_token TEXT, lease_expires REAL, available_at REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL, UNIQUE(owner_user_id, kind, input_hash)
);
CREATE INDEX jobs_dispatch ON jobs(status, available_at, created_at);
CREATE TABLE job_attempts (
    id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    lease_token TEXT NOT NULL, status TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT
);
CREATE TABLE import_batches (
    id TEXT PRIMARY KEY, owner_user_id TEXT NOT NULL REFERENCES users(id),
    adapter TEXT NOT NULL, preview TEXT NOT NULL, records TEXT, metadata TEXT NOT NULL,
    input_hash TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(owner_user_id, input_hash)
);
CREATE TABLE evaluation_configs (
    id TEXT PRIMARY KEY, settings TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE evaluation_runs (
    id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE, method TEXT NOT NULL,
    purpose TEXT NOT NULL, snapshot_hash TEXT NOT NULL, event_cutoff INTEGER NOT NULL,
    config_version TEXT NOT NULL REFERENCES evaluation_configs(id),
    parent_run_id TEXT REFERENCES evaluation_runs(id), status TEXT NOT NULL, error TEXT, created_at TEXT NOT NULL
);
CREATE INDEX evaluation_runs_job ON evaluation_runs(job_id, status);
CREATE INDEX evaluation_runs_session ON evaluation_runs(session_id, method, purpose);
CREATE TABLE report_versions (
    id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES evaluation_runs(id) ON DELETE CASCADE,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE, method TEXT NOT NULL,
    purpose TEXT NOT NULL, version INTEGER NOT NULL, parent_id TEXT REFERENCES report_versions(id),
    kind TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL,
    UNIQUE(run_id, version)
);
CREATE TABLE effective_reports (
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    method TEXT NOT NULL, purpose TEXT NOT NULL,
    report_id TEXT NOT NULL UNIQUE REFERENCES report_versions(id) ON DELETE CASCADE,
    released_to_student INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(session_id, method, purpose)
);
CREATE TABLE annotations (
    id TEXT PRIMARY KEY, report_id TEXT NOT NULL REFERENCES report_versions(id) ON DELETE CASCADE,
    dimension TEXT NOT NULL, type TEXT NOT NULL, text TEXT NOT NULL, confidence REAL NOT NULL
);
CREATE TABLE evidence_links (
    annotation_id TEXT NOT NULL REFERENCES annotations(id) ON DELETE CASCADE,
    event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE, excerpt TEXT NOT NULL,
    PRIMARY KEY(annotation_id, event_id)
);
CREATE TABLE dimension_results (
    report_id TEXT NOT NULL REFERENCES report_versions(id) ON DELETE CASCADE,
    dimension TEXT NOT NULL, state TEXT NOT NULL, level INTEGER, rationale TEXT NOT NULL,
    PRIMARY KEY(report_id, dimension),
    CHECK((state='scored' AND level BETWEEN 0 AND 3 AND level IS NOT NULL) OR
          (state IN ('not_applicable','insufficient_evidence') AND level IS NULL))
);
CREATE TABLE outcome_results (
    report_id TEXT PRIMARY KEY REFERENCES report_versions(id) ON DELETE CASCADE,
    total REAL, item_results TEXT NOT NULL, completeness INTEGER NOT NULL
);
CREATE TABLE diagnoses (
    report_id TEXT PRIMARY KEY REFERENCES report_versions(id) ON DELETE CASCADE,
    process_band TEXT, outcome_band TEXT, label TEXT
);
CREATE TABLE reviews (
    id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES evaluation_runs(id) ON DELETE CASCADE,
    report_id TEXT NOT NULL REFERENCES report_versions(id) ON DELETE CASCADE,
    reviewer_id TEXT NOT NULL REFERENCES users(id), decision TEXT NOT NULL,
    reason TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE review_changes (
    review_id TEXT NOT NULL REFERENCES reviews(id) ON DELETE CASCADE, target TEXT NOT NULL,
    before TEXT NOT NULL, after TEXT NOT NULL, PRIMARY KEY(review_id, target)
);
CREATE TABLE outbox (
    submission_id TEXT PRIMARY KEY REFERENCES submissions(id) ON DELETE CASCADE,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    config_version TEXT NOT NULL REFERENCES evaluation_configs(id), processed_at TEXT
);
CREATE TABLE exports (
    id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE REFERENCES jobs(id) ON DELETE CASCADE,
    owner_user_id TEXT NOT NULL REFERENCES users(id), source_id TEXT REFERENCES data_sources(id) ON DELETE CASCADE,
    format TEXT NOT NULL, content TEXT, expires_at REAL NOT NULL
);
CREATE TABLE deletion_audit (
    id TEXT PRIMARY KEY, actor_id TEXT NOT NULL REFERENCES users(id),
    object_type TEXT NOT NULL, counts TEXT NOT NULL, created_at TEXT NOT NULL
);
INSERT INTO users VALUES
    ('student-demo','student','演示学生',1), ('student-alt','student','演示学生二',1),
    ('teacher-demo','teacher','演示教师',1), ('teacher-alt','teacher','演示教师二',1),
    ('admin-demo','admin','演示管理员',1);
INSERT INTO courses VALUES ('demo-course','计算机协同解题演示课程','teacher-demo');
INSERT INTO course_memberships VALUES
    ('demo-course','teacher-demo','teacher','active'), ('demo-course','student-demo','student','active');
