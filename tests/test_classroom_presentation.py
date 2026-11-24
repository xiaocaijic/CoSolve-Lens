import json
from uuid import uuid4

import pytest

from backend.app import five_provider
from backend.app.core import Problem, database, dump, now, uid
from backend.app.evaluation import evaluate, event_payloads, store_report
from backend.app.five_provider import PILOT_SCOPE
from backend.app.jobs import enqueue, process_one, schedule_evaluation
from backend.app.outcomes import outcome_fields
from backend.app.studychat_quality import quality_check, quarantine_existing
from test_deepseek_studychat import activate, bundle, incomplete_csv_bundle, qualified_bundle
from test_five_rubric import structured_model_result
from test_p0 import login, system
from test_workflows import get_result


def start_inputs(scope, check):
    return {**scope, "checked_scope": check["checked_scope"], "request_key": str(uuid4())}


def test_versioned_task_library_and_local_materials(system):
    settings, app = system
    student = login(app, "student-demo")
    tasks = student.get("/api/task-definitions").json()["items"]
    assert len(tasks) == 10
    assert [task["difficulty"] for task in tasks].count("中级") == 5
    assert [task["difficulty"] for task in tasks].count("初级") == 2
    assert [task["difficulty"] for task in tasks].count("较难") == 3
    assert {task["difficulty"] for task in tasks} == {"初级", "中级", "较难"}
    for task in tasks:
        assert task["goal"] and task["materials"] and task["deliverables"] and task["ai_allowed"]
        if not task["static_demo"]:
            assert "CC BY 4.0" in task["attribution"]
        for file in task["materials"]:
            asset = student.get(f"/api/task-definitions/{task['id']}/material", params={"file": file})
            assert asset.status_code == 200 and asset.content and "attachment" in asset.headers["Content-Disposition"]
        assert student.get(f"/api/task-definitions/{task['id']}/material", params={"file": "../app.sqlite3"}).status_code == 404
    chosen = next(task for task in tasks if "SearchComplete" in task["title"])
    response = student.post("/api/live-sessions", json={"task_id": chosen["id"], "client_event_id": "library-start"})
    assert response.status_code == 201
    work = student.get("/api/live-sessions/" + response.json()["id"]).json()
    assert work["task"]["check_label"] == "边界样例与预期输出"
    energy = next(task for task in tasks if task["id"] == "dorm-energy-v1")
    assert energy["materials"] == ["energy.csv"] and "泄漏" in energy["goal"]


def test_incomplete_csv_is_excluded_and_existing_report_quarantined(system, incomplete_csv_bundle):
    settings, app = system
    teacher, student = login(app), login(app, "student-demo")
    source = activate(settings, teacher)
    options = teacher.get(f"/api/datasets/{source}/analysis-options").json()
    sid = options["items"][0]["id"]
    body = {"source_id": source, "session_ids": [sid], "model": "rules", "max_count": 1, "term": "F24", "assignment": "A1"}
    assert student.post("/api/analysis/inspect", json=body).status_code == 403
    checked = teacher.post("/api/analysis/inspect", json=body).json()
    assert checked["count"] == 1 and checked["messages"] == 4 and checked["turns"] == 2
    assert checked["grade_matches"] == 1 and checked["submission_matches"] == 0 and checked["expected_calls"] == 0
    quality = checked["sessions"][0]["quality"]
    assert (quality["observed_turns"], quality["declared_turns"], quality["completeness_ratio"]) == (2, 3, .6667)
    assert quality["eligibility_status"] == "incomplete" and quality["task_relevance"] == "irrelevant"
    assert "off_task" in quality["exclusion_reasons"] and not quality["grade_link_valid"]
    assert teacher.post("/api/analysis/start", json=start_inputs(body, checked)).json()["code"] == "STUDYCHAT_CHECK_ONLY"
    remote = body | {"model": "deepseek-flash"}
    remote_check = teacher.post("/api/analysis/inspect", json=remote).json()
    assert teacher.post("/api/analysis/start", json=start_inputs(remote, remote_check)).json()["code"] == "STUDYCHAT_INELIGIBLE"
    with database(settings, write=True) as db:
        assert db.execute("SELECT count(*) FROM evaluation_runs").fetchone()[0] == 0
        session = db.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
        old_job = schedule_evaluation(db, "teacher-demo", [session], "rules", "research", source_id=source)
        run = db.execute("SELECT * FROM evaluation_runs WHERE job_id=?", (old_job["id"],)).fetchone()
        task = db.execute("SELECT * FROM task_definitions WHERE id=?", (session["task_id"],)).fetchone()
        config = json.loads(db.execute("SELECT settings FROM evaluation_configs WHERE id=?", (run["config_version"],)).fetchone()[0])
        report = evaluate(run, session, task, event_payloads(db, sid, run["event_cutoff"]), config, False, False, settings)
        saved = store_report(db, run, report)
        db.execute("UPDATE jobs SET status='completed' WHERE id=?", (old_job["id"],))
        quarantine_existing(db)
        assert db.execute("SELECT count(*) FROM report_versions WHERE id=?", (saved["id"],)).fetchone()[0] == 1
    assert teacher.get(f"/api/datasets/{source}/excluded-reports").json()["items"][0]["status"] == "quality_rejected"
    assert teacher.get(f"/api/sessions/{sid}").json()["external_scores"] == {}
    assert teacher.get(f"/api/datasets/{source}/results").json()["total"] == 0
    assert teacher.get("/api/analytics/overview", params={"source_id": source}).json()["report_count"] == 0
    exp = teacher.post("/api/exports", json={"source_id": source, "format": "json", "acknowledge_scope": True}).json()
    assert process_one(settings)
    export = teacher.get(f"/api/jobs/{exp['id']}").json()["result"]["export_id"]
    assert teacher.get(f"/api/exports/{export}/download").json()["rows"] == []
    assert outcome_fields(0, studychat=False)["display_outcome_score"] == 0
    assert outcome_fields(studychat=True)["outcome_basis"] is None


def test_waiting_messages_and_missing_response_are_not_eligible():
    pairs = [("Hello", "Hi."), ("Please wait", "Sure."), ("Just a moment", "Okay."), ("Thanks", "You are welcome.")]
    events = [{"id": f"{turn}-{role}", "type": f"chat_{role}_message", "payload": {"text": text,
        "source_turn": turn, "conversation": 1, "studychat_source": {"chat_id": "idle", "declared_turns": 4}}}
        for turn, pair in enumerate(pairs) for role, text in zip(("user", "ai"), pair)]
    task = {"instructions": "# Assignment 1 - Future of Work"}
    status = lambda rows: quality_check(task, rows, grade_matched=True, submission_matched=False)["eligibility_status"]
    events[4]["payload"]["text"] = "One moment"
    assert status(events) == "too_short"
    assert status(events[:3] + events[4:]) == "incomplete"


def test_studychat_entry_settings_are_admin_only_and_live_gate_blocks_start(system, qualified_bundle):
    settings, app = system
    teacher, admin, student = login(app), login(app, "admin-demo"), login(app, "student-demo")
    assert student.get("/api/studychat/entry-settings").status_code == 403
    original = teacher.get("/api/studychat/entry-settings").json()
    assert original == {"replay_enabled": True, "live_enabled": True, "default_mode": "replay"}
    source = activate(settings, teacher)
    sid = teacher.get(f"/api/datasets/{source}/analysis-options").json()["items"][0]["id"]
    scope = {"source_id": source, "session_ids": [sid], "model": "deepseek-flash", "max_count": 1}
    body = original | {"live_enabled": False}
    assert teacher.put("/api/settings/studychat-entries", json=body).status_code == 403
    assert admin.put("/api/settings/studychat-entries", json=body | {"default_mode": "live"}).status_code == 422
    assert admin.put("/api/settings/studychat-entries", json=body).status_code == 200
    check = admin.post("/api/analysis/inspect", json=scope).json()
    assert "关闭 StudyChat 现场分析" in check["blocked_reason"]
    start = admin.post("/api/analysis/start", json=start_inputs(scope, check) | {"confirm_model_scope": True})
    assert start.json()["code"] == "STUDYCHAT_LIVE_DISABLED"
    with database(settings) as db:
        assert db.execute("SELECT count(*) FROM evaluation_runs").fetchone()[0] == 0
    assert admin.put("/api/settings/studychat-entries",
                     json={"replay_enabled": False, "live_enabled": True, "default_mode": "live"}).status_code == 200
    assert teacher.get("/api/studychat/showcase/bayes-condition").json()["code"] == "SHOWCASE_DISABLED"


@pytest.mark.parametrize("failure", [None, "zero_calls", "transport", "invalid"])
def test_live_analysis_requires_existing_scope_and_counts_real_transport(system, qualified_bundle, monkeypatch, failure):
    settings, app = system
    teacher, admin = login(app), login(app, "admin-demo")
    source = activate(settings, teacher)
    sid = teacher.get(f"/api/datasets/{source}/analysis-options").json()["items"][0]["id"]
    body = {"source_id": source, "session_ids": [sid], "model": "deepseek-flash", "max_count": 1}
    monkeypatch.setenv("DEEPSEEK_API_KEY", "isolated-mocked-key")
    assert admin.put("/api/settings/llm", json={"provider": "deepseek", "model": "deepseek-flash"}).status_code == 200
    assert admin.post("/api/analysis/inspect", json=body).json()["blocked_reason"]
    assert admin.put("/api/settings/llm", json={"provider": "deepseek", "model": "deepseek-flash", "enabled": True,
        "retention_confirmed": True, "usage_note": "Test only"}).status_code == 200
    assert "授权" in admin.post("/api/analysis/inspect", json=body).json()["blocked_reason"]
    with database(settings, write=True) as db:
        job, _ = enqueue(db, "admin-demo", "evaluation", {"engineering_fixture": uid()}, 1, source_id=source)
        db.execute("UPDATE jobs SET status='completed' WHERE id=?", (job["id"],))
        db.execute("INSERT INTO studychat_sample_members VALUES (?,?,?,?,?,?,?,?)", (
            job["id"], sid, "deepseek-flash", PILOT_SCOPE, "测试专用范围", dump({"matched_files": 0}), 10, now()))
    assert teacher.post("/api/analysis/inspect", json=body).json()["blocked_reason"]
    check = admin.post("/api/analysis/inspect", json=body).json()
    assert (check["expected_calls"], check["maximum_calls"]) == (2, 5)
    data = start_inputs(body, check)
    assert admin.post("/api/analysis/start", json=data).status_code == 422
    sent = []

    def fake_call(profile, key, messages, limit):
        assert "assignment_normalized" not in messages[1]["content"] and "0.92" not in messages[1]["content"]
        sent.append(profile["model"])
        if failure == "transport":
            raise Problem(503, "PROVIDER_TRANSPORT", "test connection failure")
        if limit == 1200:
            mark = "irrelevant" if failure == "mismatch" else "relevant"
            return dump({"relevance": mark, "text": "检查对话与前缀搜索的关系。", "event_ids": ["E0001"]}), {"id": "relevance"}
        assert "teacher_recommendations" in messages[0]["content"]
        result = structured_model_result()
        if failure == "invalid":
            result["dimensions"][0]["event_ids"] = ["E9999"]
        return dump(result), {"model": "deepseek-flash", "id": "mocked", "usage": {}}

    monkeypatch.setattr(five_provider, "request_completion", fake_call)
    started = admin.post("/api/analysis/start", json=data | {"confirm_model_scope": True})
    assert started.status_code == 202, started.text
    if failure == "zero_calls":
        monkeypatch.setattr(five_provider, "semantic_evaluation", lambda *args, **kwargs: (None, None))
        from backend.app import five_evaluation
        monkeypatch.setattr(five_evaluation, "semantic_evaluation", five_provider.semantic_evaluation)
    assert process_one(settings)
    job = admin.get(f"/api/jobs/{started.json()['id']}").json()
    if failure:
        assert job["status"] == "failed" and not job["runs"][0]["report_id"] and not job["runs"][0]["provider_response_valid"]
        if failure == "zero_calls":
            assert job["runs"][0]["actual_model_calls"] == 0
        return
    assert job["status"] == "completed" and job["analysis"]["provider_calls_started"] == 2 and sent == ["deepseek-flash"] * 2
    assert job["runs"][0]["actual_model_calls"] == 2 and job["runs"][0]["provider_response_valid"] == 1
    report = get_result(admin, job["runs"][0]["id"])
    assert len(report["teacher_recommendations"]) == len(report["student_recommendations"]) == 3
    assert len(report["transfer_tasks"]) == 1
    assert all("?" not in item["action"] and "？" not in item["action"] for item in report["student_recommendations"])
    with database(settings) as db:
        saved = json.loads(db.execute("SELECT body FROM report_versions WHERE id=?", (report["id"],)).fetchone()[0])
        assert saved["outcome_basis"] == "source_grade" and "outcome_display_projection" not in saved
    failure = "mismatch"
    second = admin.post("/api/analysis/start", json=start_inputs(body, check) | {"confirm_model_scope": True}).json()
    assert process_one(settings)
    failed = admin.get(f"/api/jobs/{second['id']}").json()
    assert failed["runs"][0]["report_id"] is None and failed["runs"][0]["actual_model_calls"] == 1
    assert failed["status"] == "failed"
    admin.post("/api/analysis/start", json=start_inputs(body, check) | {"confirm_model_scope": True})
    detail = teacher.get(f"/api/sessions/{sid}").json()
    assert detail["studychat"]["quality"]["task_relevance"] == "irrelevant"
    assert detail["reports"] == [] and detail["external_scores"] == {}
    assert teacher.get("/api/analytics/overview", params={"source_id": source}).json()["report_count"] == 0
