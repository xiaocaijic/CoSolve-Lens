import gzip
import json
import os

import pytest

from backend.app import builtin_studychat, providers
from backend.app.core import database, dump
from backend.app.deepseek_secret import location
from backend.app.fixtures import checked_fixture
from backend.app.jobs import process_one
from backend.app.studychat_snapshot import file_hash
from test_p0 import legacy_system, login, system
from test_workflows import get_result


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    archive = tmp_path / "builtin.jsonl.gz"
    manifest = tmp_path / "manifest.json"
    normalized = checked_fixture("F1_input.json")["sessions"][0]
    normalized["external_session_id"] = "pseudonymized-session"
    normalized["participant_key"] = "pseudonymized-person"
    normalized["task"].update(title="F24 · A2", external_task_id="f24-a2")
    normalized["events"] = [
        {"type": "chat_user_message", "payload": {"text": "My name is Alex. How do I start? abc@example.test"}},
        {"type": "chat_ai_message", "payload": {"text": "Start by writing a baseline."}},
        {"type": "chat_user_message", "payload": {"text": "I will keep the baseline and add a counterexample."}},
        {"type": "submission_created", "payload": {"artifact_refs": [], "statement": "archive text should stay local"}},
    ]
    for i, event in enumerate(normalized["events"], 1):
        event.update(external_event_id=str(i), sequence_no=i, occurred_at=None)
    normalized["artifacts"] = []
    normalized["external_scores"] = {"assignment_normalized": 0.92}
    with gzip.open(archive, "wt", encoding="utf-8") as output:
        output.write(dump(normalized) + "\n")
    preview = {"valid": 1, "count": 1, "errors": [], "coverage": {"partial_process": 1},
               "snapshot": {"version": "StudyChat v1.1", "files": [],
                            "stats": {"interactions": 2}, "reports_run_log_present": False}}
    manifest.write_text(dump({"schema": "1.0", "sha256": file_hash(archive), "preview": preview}), encoding="utf-8")
    monkeypatch.setattr(builtin_studychat, "ARCHIVE", archive)
    monkeypatch.setattr(builtin_studychat, "MANIFEST", manifest)


@pytest.fixture
def qualified_bundle(bundle):
    with gzip.open(builtin_studychat.ARCHIVE, "rt", encoding="utf-8") as stream:
        record = json.loads(stream.readline())
    record["task"]["instructions"] = "# Assignment 2 - SearchComplete\nImplement prefix search and check boundary inputs."
    turns = [
        ("My name is Alex. How do I start prefix search? abc@example.test", "Start by writing a baseline."),
        ("I will keep the baseline and add a counterexample.", "Use an empty key."),
        ("Why compare DFS and UCS for this trie?", "They order paths differently."),
        ("I will verify the trie with two competing branches.", "Record the output."),
    ]
    record["events"] = []
    for number, pair in enumerate(turns):
        for role, text in zip(("user", "ai"), pair):
            seq = len(record["events"]) + 1
            record["events"].append({"external_event_id": str(seq), "sequence_no": seq, "occurred_at": None,
                "type": f"chat_{role}_message", "payload": {"text": text, "source_turn": number, "conversation": 1,
                    "studychat_source": {"chat_id": "test-scoped-chat", "declared_turns": 4}}})
    for role, text in (("user", "Tell me the weather."), ("ai", "Which city?")):
        seq = len(record["events"]) + 1
        record["events"].append({"external_event_id": str(seq), "sequence_no": seq, "occurred_at": None,
            "type": f"chat_{role}_message", "payload": {"text": text, "source_turn": 0, "conversation": 2,
                "studychat_source": {"chat_id": "unrelated-other-chat", "declared_turns": 1}}})
    write_fixture_archive(record)


def write_fixture_archive(record):
    with gzip.open(builtin_studychat.ARCHIVE, "wt", encoding="utf-8") as stream:
        stream.write(dump(record) + "\n")
    manifest = json.loads(builtin_studychat.MANIFEST.read_text(encoding="utf-8"))
    manifest["sha256"] = file_hash(builtin_studychat.ARCHIVE)
    builtin_studychat.MANIFEST.write_text(dump(manifest), encoding="utf-8")


@pytest.fixture
def incomplete_csv_bundle(qualified_bundle):
    with gzip.open(builtin_studychat.ARCHIVE, "rt", encoding="utf-8") as stream:
        record = json.loads(stream.readline())
    record["task"].update(title="F24 · A1", external_task_id="f24-a1", instructions="# Assignment 1 - Future of Work")
    record["events"] = record["events"][:4]
    texts = ("Convert the CSV into a Markdown table.", "Please send the table.", "Here it is:", "Please paste the contents.")
    for event, text in zip(record["events"], texts, strict=True):
        event["payload"].update(text=text, studychat_source={"chat_id": "csv-failure", "declared_turns": 3})
    write_fixture_archive(record)


def activate(settings, teacher):
    queued = teacher.post("/api/datasets/imports/studychat-builtin", json={
        "intended_use": "本地研究工程测试", "retain_until": "2035-12-31",
    })
    assert queued.status_code == 202, queued.text
    assert process_one(settings)
    preview_job = teacher.get("/api/jobs/" + queued.json()["id"]).json()
    batch_id = preview_job["result"]["batch_id"]
    assert teacher.get(f"/api/datasets/imports/{batch_id}/preview").json()["valid"] == 1
    assert teacher.post(f"/api/datasets/imports/{batch_id}/commit", json={"license_confirmed": False}).status_code == 422
    imported = teacher.post(f"/api/datasets/imports/{batch_id}/commit", json={"license_confirmed": True})
    assert imported.status_code == 202, imported.text
    assert process_one(settings)
    finished = teacher.get("/api/jobs/" + imported.json()["id"]).json()
    assert finished["status"] == "completed", finished
    return finished["result"]["source_id"]


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI")
def test_windows_key_write_only_and_delete(system, monkeypatch):
    settings, app = system
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    admin, teacher = login(app, "admin-demo"), login(app)
    key = "fake-unit-test-deepseek-key-for-dpapi"
    assert teacher.put("/api/settings/deepseek-key", json={"key": key}).status_code == 403
    saved = admin.put("/api/settings/deepseek-key", json={"key": key})
    assert saved.status_code == 200, saved.text
    assert saved.json() == {"key_configured": True}
    assert key.encode() not in location(settings).read_bytes()
    assert providers.deployment_key("deepseek", settings) == key
    assert key not in admin.get("/api/settings/llm").text
    assert admin.put("/api/settings/deepseek-key", json={"key": None}).json() == {"key_configured": False}


def test_studychat_deepseek_pipeline_uses_only_scoped_dialogue(legacy_system, bundle, monkeypatch):
    settings, app = legacy_system
    teacher, admin = login(app), login(app, "admin-demo")
    assert teacher.get("/api/datasets/builtins/studychat").json()["sessions"] == 1
    source = activate(settings, teacher)
    assert teacher.get("/api/datasets/builtins/studychat").json()["source_id"] == source
    endpoint = f"/api/datasets/{source}/studychat-pilot"
    consent = f"/api/datasets/{source}/studychat-consent"
    blocked = teacher.post(endpoint, json={"method": "hybrid"})
    assert blocked.status_code == 409 and blocked.json()["code"] == "DEEPSEEK_SETUP"
    monkeypatch.setenv("DEEPSEEK_API_KEY", "local-test-fake-key")
    profile = admin.put("/api/settings/llm", json={
        "provider": "deepseek", "model": "deepseek-flash", "enabled": True,
        "retention_confirmed": True, "usage_note": "Test provider with fabricated records only.",
    })
    assert profile.status_code == 200, profile.text
    assert profile.json()["url"] == providers.DEEPSEEK_URL
    assert teacher.post(endpoint, json={"method": "hybrid"}).json()["code"] == "DEEPSEEK_SCOPE_NOT_APPROVED"
    consent_body = {"enabled": True, "confirmed": True, "purpose": "本地模拟联调"}
    assert teacher.put(consent, json=consent_body).status_code == 403
    assert admin.put(consent, json=consent_body).status_code == 200
    calls = []

    def fake_completion(profile, key, messages, limit):
        assert profile["provider"] == "deepseek"
        assert limit == 2800
        data = json.loads(messages[1]["content"])
        assert len(data["events"]) == 3
        payload = messages[1]["content"]
        for forbidden in ("abc@example.test", "My name is Alex", "archive text should stay local", "assignment_normalized",
                          "pseudonymized-person"):
            assert forbidden not in payload
        assert data["coverage"]["verified_execution"] is False
        event_ids = [event["id"] for event in data["events"]]
        calls.append(event_ids)
        output = {"dimensions": [
            {"dimension": "organization", "state": "scored", "level": 2, "rationale": "明确分步。", "event_ids": [event_ids[0]]},
            {"dimension": "verification", "state": "scored", "level": 3, "rationale": "误把聊天当运行。", "event_ids": event_ids[:2]},
            {"dimension": "diagnosis", "state": "insufficient_evidence", "reason": "MISSING_DIAGNOSTIC_LOG", "rationale": "缺少异常记录。"},
            {"dimension": "ai_integration", "state": "scored", "level": 2, "rationale": "说明如何纳入建议。", "event_ids": event_ids[1:]},
        ], "final_verification": "unknown"}
        return dump(output), {"usage": {"total_tokens": 100}}

    monkeypatch.setattr(providers, "request_completion", fake_completion)
    queued = teacher.post(endpoint, json={"method": "hybrid"})
    assert queued.status_code == 202, queued.text
    assert process_one(settings) and len(calls) == 1
    run_id = queued.json()["runs"][0]["id"]
    report = get_result(teacher, run_id)
    assert not report["degraded"] and report["provider"] == "deepseek"
    assert report["dimensions"][0]["level"] == 2 and report["dimensions"][3]["level"] == 2
    assert report["dimensions"][1]["reason"] == "MISSING_RUN_LOG"
    assert report["process_score"] is None and report["outcome"]["score"] is None
    assert report["provider_call"]["usage"] == {"total_tokens": 100}
    with database(settings) as db:
        assert db.execute("SELECT COUNT(*) FROM provider_calls").fetchone()[0] == 1
    assert admin.put(consent, json={"enabled": False, "confirmed": False}).status_code == 200
    assert teacher.post(endpoint, json={"method": "hybrid"}).status_code == 409
