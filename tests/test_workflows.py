import json
import sqlite3

import pytest

from backend.app.core import ROOT, Problem, database, digest, dump
from backend.app.fixtures import checked_fixture
from backend.app.jobs import consume_outbox, process_one
from backend.app.providers import input_payload, validate_url
from test_p0 import enqueue_runs, import_demo, legacy_system, login, system


def get_result(client, run_id, report_id=None):
    params = {"report_id": report_id} if report_id else {}
    response = client.get("/api/evaluation-runs/" + run_id, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def event_keys(settings, session_id):
    with database(settings) as db:
        return {row["id"]: row["event_key"] for row in db.execute("SELECT id,event_key FROM events WHERE session_id=?", (session_id,))}


def assert_expected(report, expected, keys):
    assert report["process_score"] == expected["process_score"]
    assert report["diagnosis"]["label"] == expected["diagnosis"]
    if "outcome_score" in expected:
        assert report["outcome"]["score"] == expected["outcome_score"]
        assert [item["score"] for item in report["outcome"]["items"]] == expected["outcome_items"]
    for actual, desired in zip(report["dimensions"], expected["dimensions"], strict=True):
        assert (actual["dimension"], actual["state"], actual["level"]) == (
            desired["dimension"], desired["state"], desired["level"])
        if "reason" in desired:
            assert actual["reason"] == desired["reason"]
        if "event_keys" in desired:
            assert [keys[event_id] for event_id in actual["event_ids"]] == desired["event_keys"]


@pytest.mark.parametrize("method", ["rules", "direct_llm", "hybrid"])
def test_six_goldens(legacy_system, method):
    settings, app = legacy_system
    teacher = login(app)
    source, sessions = import_demo(settings, teacher)
    queued = enqueue_runs(teacher, source, list(sessions.values()), method)
    assert process_one(settings)
    job = teacher.get("/api/jobs/" + queued["id"]).json()
    assert job["succeeded_count"] == 6, job
    reports = {run["session_id"]: get_result(teacher, run["id"]) for run in job["runs"]}
    for case, session_id in sessions.items():
        report = reports[session_id]
        file = case + ("_rules_expected.json" if method == "rules" else "_demo_expected.json")
        assert_expected(report, checked_fixture(file), event_keys(settings, session_id))
        assert report["demo_only"] is True
        assert report["degraded"] is (method != "rules")
        assert all(item["evidence_refs"] for item in report["annotations"])


def test_conflict_review_audit_immutable_and_export(legacy_system):
    settings, app = legacy_system
    teacher = login(app)
    source, sessions = import_demo(settings, teacher)
    run_id = enqueue_runs(teacher, source, [sessions["F5"]], "demo_conflict")["runs"][0]["id"]
    process_one(settings)
    initial = get_result(teacher, run_id)
    keys = event_keys(settings, sessions["F5"])
    expected = checked_fixture("F5C_initial_expected.json")
    assert_expected(initial, expected, keys)
    conflict = initial["conflicts"][0]
    assert [keys[i] for i in conflict["candidate"]["event_ids"]] == expected["conflict"]["claim_evidence"]
    assert [keys[i] for i in conflict["rule_event_ids"]] == expected["conflict"]["rule_evidence"]
    assert initial["teaching_suggestions"] == []
    assert initial["raw_rule_dimensions"][1]["level"] == 3
    assert initial["raw_llm_candidate"]["dimensions"][1]["level"] == 1
    assert teacher.get("/api/reviews/queue?state=conflict").json()["total"] == 1
    url = f"/api/evaluation-runs/{run_id}/reviews"
    body = {"expected_report_version": initial["id"], "reason": "最终快照关联通过测试和提交记录。", "decision": "amend",
            "conflict_resolution": "accept_rule", "adjudication_event_ids": conflict["rule_event_ids"]}
    assert teacher.post(url, json={**body, "adjudication_event_ids": [next(iter(event_keys(settings, sessions["F1"])))]}).status_code == 422
    assert login(app, "teacher-alt").post(url, json=body).status_code == 403
    review = teacher.post(url, json=body)
    assert review.status_code == 201, review.text
    final = review.json()
    assert_expected(final, checked_fixture("F5C_reviewed_expected.json"), keys)
    assert initial["id"] == final["parent_id"]
    assert final["version"] == 2 and final["effective"]
    assert final["conflicts"][0]["state"] == "resolved"
    assert teacher.post(url, json=body).status_code == 409
    saved_initial = get_result(teacher, run_id, initial["id"])
    assert saved_initial["raw_llm_candidate"] == final["raw_llm_candidate"]
    assert saved_initial["process_score"] is None and not saved_initial["effective"]
    history = teacher.get(f"/api/evaluation-runs/{run_id}/versions").json()["items"]
    assert {change["target"] for change in history[0]["changes"]} >= {"conflicts", "dimension:verification"}
    with database(settings, write=True) as db, pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute("UPDATE report_versions SET body='{}' WHERE id=?", (initial["id"],))
    export_data = {"source_id": source, "format": "json", "include_demo": True, "acknowledge_scope": True}
    assert teacher.post("/api/analytics/validation-exports", json=export_data).status_code == 422
    export = teacher.post("/api/exports", json=export_data).json()
    assert process_one(settings)
    download_id = teacher.get("/api/jobs/" + export["id"]).json()["result"]["export_id"]
    downloaded = teacher.get(f"/api/exports/{download_id}/download").json()
    assert downloaded["demo_only"]
    assert downloaded["rows"][0]["report"]["review_status"] == "amend"
    assert "context" not in downloaded["rows"][0]["events"][0]
    assert login(app, "teacher-alt").get(f"/api/exports/{download_id}/download").status_code == 403
    assert teacher.get("/api/analytics/overview").json()["sample_count"] == 0
    assert teacher.get("/api/analytics/overview?scope=demo").json()["sample_count"] == 1


def launch_live(client):
    result = client.post("/api/live-sessions", json={"task_id": "campus-sales-v1", "client_event_id": "start"})
    assert result.status_code == 201, result.text
    return result.json()["id"]


def test_live_full_flow_and_object_scope(system):
    settings, app = system
    student, peer = login(app, "student-demo"), login(app, "student-alt")
    teacher, outsider = login(app), login(app, "teacher-alt")
    assert not any("hidden" in key for key in student.get("/api/task-definitions").json()["items"][0])
    sid = launch_live(student)
    assert launch_live(student) == sid
    live = "/api/live-sessions/" + sid
    assert peer.get(live).status_code == 403
    assert outsider.get(live).status_code == 403
    assert student.get("/api/settings/llm").status_code == 403
    assert student.post("/api/settings/provider-check").status_code == 403
    chat = student.post(live + "/chat", json={"text": "如何处理退款？", "client_event_id": "chat-1"})
    assert chat.json()["simulated"]
    bad_event = student.post(live + "/events", json={"type": "test_finished", "client_event_id": "fake"})
    assert bad_event.status_code == 422
    code = "raise RuntimeError('this source must never be executed')"
    draft = student.post(live + "/events", json={"type": "code_snapshot", "content": code,
                         "expected_artifact_version": 0, "client_event_id": "draft-1"})
    assert draft.status_code == 200, draft.text
    assert draft.json()["version"] == 1
    sample = {"content": code, "artifact_checksum": digest(code), "expected_artifact_version": 1}
    rejected = student.post(live + "/runs", json={**sample, "expected_artifact_version": 0, "client_event_id": "run-0"})
    assert (rejected.status_code, rejected.json()["code"]) == (409, "ARTIFACT_VERSION")
    runner = student.post(live + "/runs", json={**sample, "client_event_id": "run-1"})
    assert runner.status_code == 200, runner.text
    assert runner.json()["run"]["synthetic"] and not runner.json()["run"]["verified"]
    new_code = code + "\n# edited after run"
    submit = {**sample, "content": new_code, "artifact_checksum": digest(new_code), "client_event_id": "submit-1",
              "cleaned_data": "order_id,amount\n002,-8\n", "trend": "01,20", "statement": "保留退款。"}
    first = student.post(live + "/submit", json=submit)
    assert first.status_code == 202, first.text
    assert student.post(live + "/submit", json=submit).json() == first.json()
    assert student.post(live + "/submit", json={**submit, "statement": "changed"}).status_code == 409
    assert student.post(live + "/events", json={"type": "editor_focus", "client_event_id": "late"}).status_code == 409
    with database(settings) as db:
        assert db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM evaluation_runs").fetchone()[0] == 0
    consume_outbox(settings)
    consume_outbox(settings)
    assert process_one(settings)
    assert not process_one(settings)
    entry = student.get(f"/api/sessions/{sid}").json()
    run_id = entry["reports"][0]["run_id"]
    report = get_result(student, run_id)
    assert report["degraded"] and report["source_type"] == "live"
    assert next(item for item in report["dimensions"] if item["dimension"] == "result_evaluation")["reason"] == "SYNTHETIC_EXECUTION"
    assert report["process_score"] is None and report["outcome"]["score"] is None
    assert report["raw_rule_facts"]["final_execution_event_ids"] == []
    assert peer.get(f"/api/evaluation-runs/{run_id}").status_code == 403
    assert outsider.get(f"/api/evaluation-runs/{run_id}").status_code == 403
    assert teacher.get("/api/reviews/queue").json()["total"] == 1
    reviewed = teacher.post(f"/api/evaluation-runs/{run_id}/reviews", json={
        "decision": "confirm", "reason": "核对了提交版本及模拟执行标记。", "expected_report_version": report["id"],
    })
    assert reviewed.status_code == 201, reviewed.text
    assert get_result(student, run_id)["version"] == 2
    assert student.get(f"/api/evaluation-runs/{run_id}?report_id={report['id']}").status_code == 403
    assert teacher.get("/api/analytics/overview").json()["report_count"] == 0


def upload(client, adapter, data, filename, **fields):
    metadata = {"name": "授权来源", "source_identity": "testing-course", "intended_use": "工程测试",
                "license_note": "合成测试资料", "retain_until": "2035-02-01", **fields}
    return client.post("/api/datasets/imports", files={"adapter": (None, adapter), "metadata": (None, dump(metadata)),
                                                       "file": (filename, data, "application/octet-stream")})


def import_file(settings, client, response):
    assert response.status_code == 201, response.text
    preview = response.json()
    commit = client.post(f"/api/datasets/imports/{preview['id']}/commit", json={"license_confirmed": True, "only_valid": True})
    assert commit.status_code == 202, commit.text
    assert process_one(settings)
    job = client.get("/api/jobs/" + commit.json()["id"]).json()
    assert job["status"] == "completed", job
    return job["result"]["source_id"]


def test_generic_import_errors_privacy_and_legacy_execution(system):
    settings, app = system
    client = login(app)
    template = checked_fixture("F1_input.json")
    entry = template["sessions"][0]
    entry["participant_key"] = " private student 554321 "
    entry["external_session_id"] = "sensitive-origin-key"
    for event in entry["events"]:
        if event["type"] in ("run_finished", "test_finished"):
            event["payload"].pop("verified")
    broken = {**entry, "external_session_id": "other", "events": [entry["events"][0], entry["events"][0]]}
    template["sessions"].append(broken)
    uploaded = upload(client, "generic_json", dump(template).encode(), "canonical.json")
    assert uploaded.json()["errors"][0]["code"] == "DUPLICATE_SEQUENCE"
    assert uploaded.json()["valid"] == 1
    with database(settings) as db:
        serialized = db.execute("SELECT records FROM import_batches").fetchone()[0]
        assert entry["participant_key"].strip() not in serialized
        assert entry["external_session_id"] not in serialized
    source = import_file(settings, client, uploaded)
    session = client.get("/api/sessions", params={"source_id": source}).json()["items"][0]
    run_id = enqueue_runs(client, source, [session["id"]], "hybrid")["runs"][0]["id"]
    process_one(settings)
    report = get_result(client, run_id)
    assert next(item for item in report["dimensions"] if item["dimension"] == "result_evaluation")["reason"] == "UNVERIFIED_EXECUTION"
    assert report["outcome"]["score"] is None
    assert not report["demo_only"] and report["degraded"]


def test_studychat_mapping_and_no_fabricated_outcome(system):
    settings, app = system
    client = login(app)
    csv = (ROOT / "fixtures" / "studychat_turns.csv").read_bytes()
    bad = upload(client, "studychat", csv, "turns.csv")
    assert bad.status_code == 422 and "speaker" in bad.json()["details"]["headers"]
    mapping = {"participant": "learner", "task": "assignment", "session": "conversation",
               "role": "speaker", "text": "message", "turn": "turn", "labels": "tag", "score": "assignment_score"}
    response = upload(client, "studychat", csv, "turns.csv", mapping=mapping)
    assert response.json()["coverage"]["partial_process"] == 1
    source = import_file(settings, client, response)
    session = client.get("/api/sessions", params={"source_id": source}).json()["items"][0]
    run_id = enqueue_runs(client, source, [session["id"]], "hybrid")["runs"][0]["id"]
    process_one(settings)
    report = get_result(client, run_id)
    assert report["outcome"]["score"] is None
    assert report["process_score"] is None
    assert len(report["annotations"]) == 0


def test_batch_partial_failure_intact_and_retry(system, monkeypatch):
    from backend.app import jobs

    settings, app = system
    client = login(app)
    source, sessions = import_demo(settings, client)
    original = jobs.evaluate

    def fail_one(run, session, *rest):
        if session["id"] == sessions["F2"]:
            raise RuntimeError("intentional failure")
        return original(run, session, *rest)

    monkeypatch.setattr(jobs, "evaluate", fail_one)
    batch = enqueue_runs(client, source, [sessions["F1"], sessions["F2"]], "hybrid")
    assert process_one(settings)
    result = client.get("/api/jobs/" + batch["id"]).json()
    assert (result["status"], result["succeeded_count"], result["failed_count"]) == ("partial", 1, 1)
    successful = next(run for run in result["runs"] if run["status"] == "completed")
    before = get_result(client, successful["id"])["id"]
    monkeypatch.setattr(jobs, "evaluate", original)
    assert client.post("/api/jobs/" + batch["id"] + "/retry-failed").status_code == 202
    assert process_one(settings)
    assert get_result(client, successful["id"])["id"] == before
    final = client.get("/api/jobs/" + batch["id"]).json()
    assert (final["status"], final["succeeded_count"], final["failed_count"], final["retry_count"]) == ("completed", 2, 0, 1)


def test_provider_allowlist_and_field_scanning(monkeypatch):
    for url in ("http://api.openai.com/v1/chat/completions", "https://127.0.0.1/v1/chat/completions",
                "https://api.openai.com.evil.test/v1/chat/completions",
                "https://someone@api.openai.com/v1/chat/completions"):
        with pytest.raises(Problem):
            validate_url(url)
    validate_url("https://api.openai.com/v1/chat/completions")
    event = {"id": "e1", "type": "chat_user_message", "sequence_no": 1,
             "payload": {"text": "问 test@example.com 或学号12345678", "participant": "excluded"}}
    result, count = input_payload({"instructions": "题目"}, [event], {}, None)
    assert count == 2
    assert "test@example.com" not in dump(result) and "excluded" not in dump(result)
    event["payload"]["text"] = "token = example-secret-for-unit-test"
    with pytest.raises(Problem, match="阻止外发"):
        input_payload({"instructions": "题目"}, [event], {}, None)


def test_threshold_versions_and_no_key_return(system, monkeypatch):
    settings, app = system
    teacher, admin = login(app), login(app, "admin-demo")
    monkeypatch.setenv("AI_COLLAB_LLM_KEY", "unit-test-value-never-returned")
    assert teacher.get("/api/settings/llm").status_code == 403
    response = admin.get("/api/settings/llm")
    assert response.json()["key_configured"]
    assert "unit-test-value-never-returned" not in response.text
    source, sessions = import_demo(settings, teacher)
    run_id = enqueue_runs(teacher, source, [sessions["F6"]], "hybrid")["runs"][0]["id"]
    changed = admin.put("/api/settings/evaluation", json={"process_threshold": 90, "outcome_threshold": 75})
    assert changed.status_code == 200, changed.text
    process_one(settings)
    original = get_result(teacher, run_id)
    assert original["diagnosis"]["label"] == "过程达标、成果需完善"
    rerun = teacher.post(f"/api/evaluation-runs/{run_id}/rerun", json={"expected_report_version": original["id"]})
    assert rerun.status_code == 202, rerun.text
    process_one(settings)
    derived = get_result(teacher, rerun.json()["runs"][0]["id"])
    assert derived["diagnosis"]["label"] == "过程与成果均需支持"
    assert derived["threshold_version"] != original["threshold_version"]
    assert get_result(teacher, run_id, original["id"])["diagnosis"] == original["diagnosis"]
