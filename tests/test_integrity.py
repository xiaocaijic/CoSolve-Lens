import csv
import json
import zipfile

from backend.app.core import database, digest, dump
from backend.app.fixtures import checked_fixture
from backend.app.jobs import consume_outbox, process_one
from test_p0 import enqueue_runs, login, system
from test_workflows import get_result, import_file, launch_live, upload


def test_synthetic_evidence_never_releases_formal_review_score(system):
    settings, app = system
    student, teacher = login(app, "student-demo"), login(app)
    sid = launch_live(student)
    live = "/api/live-sessions/" + sid
    code = "x = 2"
    snapshot = {"content": code, "artifact_checksum": digest(code), "expected_artifact_version": 0}
    assert student.post(live + "/runs", json={**snapshot, "client_event_id": "r"}).status_code == 200
    assert student.post(live + "/submit", json={**snapshot, "client_event_id": "s", "expected_artifact_version": 1}).status_code == 202
    consume_outbox(settings)
    assert process_one(settings)
    report = student.get(f"/api/sessions/{sid}").json()["reports"][0]
    timeline = student.get(f"/api/sessions/{sid}/timeline").json()["items"]
    refs = [event["id"] for event in timeline if event["type"] in ("run_finished", "test_finished")]
    amendment = teacher.post(f"/api/evaluation-runs/{report['run_id']}/reviews", json={
        "decision": "amend", "expected_report_version": report["id"], "reason": "尝试引用模拟记录。",
        "dimensions": [{"dimension": "result_evaluation", "state": "scored", "level": 3, "event_ids": refs, "rationale": "有两条执行记录。"}],
    })
    assert amendment.status_code == 422, amendment.text
    assert amendment.json()["code"] == "NON_SCOREABLE_EXECUTION"
    assert get_result(student, report["run_id"])["id"] == report["id"]


def test_unbound_submission_and_conflicting_final_tests(system):
    settings, app = system
    client = login(app)
    unbound = checked_fixture("F1_input.json")
    submission = unbound["sessions"][0]["events"][-1]
    submission["payload"]["artifact_refs"] = ["statement"]
    assert "artifact_checksum" in submission["payload"]
    source = import_file(settings, client, upload(client, "generic_json", dump(unbound).encode(), "unbound.json"))
    session = client.get("/api/sessions", params={"source_id": source}).json()["items"][0]
    run_id = enqueue_runs(client, source, [session["id"]], "rules")["runs"][0]["id"]
    assert process_one(settings)
    report = get_result(client, run_id)
    assert report["raw_rule_facts"]["final_checksum"] is None
    assert next(item for item in report["dimensions"] if item["dimension"] == "result_evaluation")["state"] == "insufficient_evidence"
    conflicted = checked_fixture("F1_input.json")
    events = conflicted["sessions"][0]["events"]
    failure = json.loads(dump(events[-2]))
    failure.update(external_event_id="conflict", sequence_no=5)
    failure["payload"].update(passed=0, failed=1, cases=[{"id": "main", "passed": False, "category": "main"}])
    events[-1]["sequence_no"] = 6
    events.insert(-1, failure)
    post = upload(client, "generic_json", dump(conflicted).encode(), "conflict.json", source_identity="conflict")
    source = import_file(settings, client, post)
    session = client.get("/api/sessions", params={"source_id": source}).json()["items"][0]
    run_id = enqueue_runs(client, source, [session["id"]], "rules")["runs"][0]["id"]
    assert process_one(settings)
    report = get_result(client, run_id)
    assert next(item for item in report["dimensions"] if item["dimension"] == "result_evaluation")["reason"] == "CONFLICTING_RECORDS"
    assert report["process_score"] is None and report["teaching_suggestions"] == []


def test_local_snapshot_import_with_full_crosswalk(system, tmp_path, monkeypatch):
    settings, app = system
    root = tmp_path / "studychat"
    for folder in ("dialogues", "scores", "submissions", "assignment_text/studychat_f24_assignments/a2"):
        (root / folder).mkdir(parents=True)
    monkeypatch.setenv("AI_COLLAB_STUDYCHAT_ROOT", str(root))
    directory = "user_7e9e3790-af00-4811-9372-db746e18a717"
    peer = "unit-peer"
    records = []
    for i in (1, 0):
        records.append({"prompt": f"input {i}", "response": f"output {i}", "topic": "a2", "chatId": "one",
                        "userId": peer, "timestamp": 1740201613871 + i * 1000, "interactionCount": i,
                        "llm_label": {"label": "planning"}, "messages": [{"role": "system", "content": "do not duplicate"}]})
    (root / "dialogues/f24_labeled_dialogues.jsonl").write_text("\n".join(map(dump, records)), encoding="utf-8")
    with (root / "scores/f24_grades_released_normalized.csv").open("w", encoding="utf-8", newline="") as stream:
        csv.writer(stream).writerows([["userId", "directory_name", "a2"], [peer, directory, "0.8"]])
    (root / "assignment_text/studychat_f24_assignments/a2/README.md").write_text("Plan and implement search.", encoding="utf-8")
    with zipfile.ZipFile(root / "submissions/studychats_student_submissions_cleaned_final.zip", "w") as archive:
        archive.writestr(f"release/f24/assignment-2-search-submissions/{directory}/solution.py", "print(1)")
        archive.writestr(f"release/s25/assignment-2-search-submissions/{directory}/wrong.py", "wrong semester")
        archive.writestr(f"release/f24/assignment-2-search-submissions/{directory}/../escape.py", "avoid traversal")
    client = login(app)
    response = client.post("/api/datasets/imports/studychat-local", json={
        "semester": "f24", "assignment": "a2", "license_note": "CC BY 4.0", "intended_use": "本地测试",
        "retain_until": "2035-12-31",
    })
    assert response.status_code == 202, response.text
    assert process_one(settings)
    preview_job = client.get("/api/jobs/" + response.json()["id"]).json()
    assert preview_job["status"] == "completed", preview_job
    batch_id = preview_job["result"]["batch_id"]
    preview = client.get(f"/api/datasets/imports/{batch_id}/preview").json()
    assert preview["snapshot"]["stats"]["linked_files"] == 1
    assert preview["snapshot"]["stats"]["interactions"] == 2
    commit = client.post(f"/api/datasets/imports/{batch_id}/commit", json={"license_confirmed": True})
    assert commit.status_code == 202, commit.text
    assert process_one(settings)
    source = client.get("/api/jobs/" + commit.json()["id"]).json()["result"]["source_id"]
    session = client.get("/api/sessions", params={"source_id": source}).json()["items"][0]
    detail = client.get(f"/api/sessions/{session['id']}").json()
    assert detail["external_scores"] == {}
    assert detail["studychat"]["quality"]["grade_matched"] is True
    assert detail["studychat"]["quality"]["grade_link_valid"] is False
    assert detail["task_instructions"] == "Plan and implement search."
    timeline = client.get(f"/api/sessions/{session['id']}/timeline").json()["items"]
    assert [event["type"] for event in timeline] == ["chat_user_message", "chat_ai_message", "chat_user_message",
                                                     "chat_ai_message", "submission_created"]
    assert timeline[0]["excerpt"] == "input 0"
    assert timeline[1]["occurred_at"] is None
    artifacts = client.get(f"/api/sessions/{session['id']}/artifacts").json()
    assert artifacts["total"] == 1
    file_id = artifacts["items"][0]["id"]
    assert client.get(f"/api/sessions/{session['id']}/artifacts/{file_id}").json()["content"] == "print(1)"
    assert login(app, "teacher-alt").get(f"/api/sessions/{session['id']}/artifacts/{file_id}").status_code == 403
    with database(settings) as db:
        assert db.execute("SELECT COUNT(*) FROM import_batch_records").fetchone()[0] == 0
        assert db.execute("SELECT allow_external_model FROM data_sources").fetchone()[0] == 0
