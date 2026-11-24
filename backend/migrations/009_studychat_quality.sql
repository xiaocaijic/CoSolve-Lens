CREATE TABLE studychat_quality_rejections (
    report_id TEXT PRIMARY KEY REFERENCES report_versions(id) ON DELETE CASCADE,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE studychat_run_quality (
    run_id TEXT PRIMARY KEY REFERENCES evaluation_runs(id) ON DELETE CASCADE,
    body TEXT NOT NULL,
    actual_model_calls INTEGER NOT NULL DEFAULT 0,
    provider_response_valid INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX studychat_quality_rejections_created ON studychat_quality_rejections(created_at);

ALTER TABLE evaluation_progress RENAME TO previous_evaluation_progress;
CREATE TABLE evaluation_progress (
    run_id TEXT PRIMARY KEY REFERENCES evaluation_runs(id) ON DELETE CASCADE,
    stage TEXT NOT NULL CHECK(stage IN ('dialogue','task_relevance','evaluation','evidence','recommendations')),
    updated_at TEXT NOT NULL
);
INSERT INTO evaluation_progress SELECT * FROM previous_evaluation_progress;
DROP TABLE previous_evaluation_progress;
