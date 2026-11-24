import pytest
from fastapi.testclient import TestClient

from backend.app.core import Settings, database, migrate
from backend.app.fixtures import checked_fixture, manifest
from backend.app.jobs import process_one
from backend.app.main import create_app


@pytest.fixture
def system(tmp_path):
    settings = Settings(database=tmp_path / "test.sqlite", demo=True, pseudonym_key="test-key")
    migrate(settings)
    return settings, create_app(settings)


@pytest.fixture
def legacy_system(system, monkeypatch):
    """Exercise the frozen four-dimension snapshots with their original contract."""
    from backend.app import jobs, live, settings_api
    from backend.app.evaluation import DEFAULT_CONFIG, initial_config

    old = DEFAULT_CONFIG | {"rubric_version": "bars-1.2", "prompt_version": "prompt-1", "ruleset_version": "rules-1"}

    def archived_config(db):
        row = db.execute("SELECT id FROM evaluation_configs ORDER BY rowid DESC LIMIT 1").fetchone()
        return row["id"] if row else initial_config(db, old)

    for module in (jobs, live, settings_api):
        monkeypatch.setattr(module, "active_config", archived_config)
    return system


def login(app, account="teacher-demo"):
    client = TestClient(app)
    response = client.post("/api/auth/demo", json={"account": account})
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf"]
    return client


def import_demo(settings, client):
    preview = client.post("/api/datasets/imports", files={"adapter": (None, "demo")})
    assert preview.status_code == 201, preview.text
    assert preview.json()["valid"] == 6
    assert client.get("/api/datasets").json()["total"] == 0
    location = "/api/datasets/imports/" + preview.json()["id"] + "/commit"
    assert client.post(location, json={"license_confirmed": False}).status_code == 422
    queued = client.post(location, json={"license_confirmed": True})
    assert queued.status_code == 202, queued.text
    assert queued.json()["status"] == "queued"
    job_id = queued.json()["id"]
    assert process_one(settings)
    done = client.get("/api/jobs/" + job_id).json()
    assert done["status"] == "completed", done
    assert client.post(location, json={"license_confirmed": True}).json()["id"] == job_id
    source_id = done["result"]["source_id"]
    sessions = client.get("/api/sessions", params={"source_id": source_id}).json()["items"]
    return source_id, {item["fixture_id"]: item["id"] for item in sessions}


def enqueue_runs(client, source, sessions, method="rules"):
    response = client.post("/api/evaluation-runs/batch", json={
        "source_id": source, "session_ids": sessions, "method": method,
    })
    assert response.status_code == 202, response.text
    return response.json()


def test_all_fixture_files_match_manifest():
    checks = manifest()
    assert checks["fixture_version"] == "1.0"
    assert len(checks["files"]) == 21
    for name in checks["files"]:
        assert checked_fixture(name)


def test_p0_import_async_report_and_evidence(legacy_system):
    settings, app = legacy_system
    client = login(app)
    source, sessions = import_demo(settings, client)
    batch = enqueue_runs(client, source, [sessions["F1"], sessions["F3"]])
    assert batch["status"] == "queued"
    assert enqueue_runs(client, source, [sessions["F3"], sessions["F1"]])["id"] == batch["id"]
    assert process_one(settings)
    done = client.get("/api/jobs/" + batch["id"]).json()
    assert (done["status"], done["succeeded_count"], done["progress_done"]) == ("completed", 2, 2)
    reports = {row["session_id"]: client.get("/api/evaluation-runs/" + row["id"]).json() for row in done["runs"]}
    for fixture in ("F1", "F3"):
        report = reports[sessions[fixture]]
        baseline = checked_fixture(f"{fixture}_rules_expected.json")
        assert report["process_score"] is None
        assert report["diagnosis"]["label"] is None
        for expected, actual in zip(baseline["dimensions"], report["dimensions"], strict=True):
            assert (actual["state"], actual["level"]) == (expected["state"], expected["level"])
        annotation = report["annotations"][0]
        evidence = client.get("/api/annotations/" + annotation["id"] + "/evidence").json()["items"]
        assert len(evidence) == len(annotation["evidence_refs"])
        item = evidence[0]
        event = client.get(f"/api/sessions/{report['session_id']}/events/{item['event_id']}").json()
        assert event["timestamp_precision"] == "sequence_only" and event["occurred_at"] is None
    assert reports[sessions["F3"]]["dimensions"][1]["level"] == 0
    with database(settings) as db:
        assert db.execute("SELECT COUNT(*) FROM report_versions").fetchone()[0] == 2


def test_p0_permissions_and_csrf(system):
    settings, app = system
    teacher = login(app)
    source, sessions = import_demo(settings, teacher)
    job = enqueue_runs(teacher, source, [sessions["F1"]])
    assert process_one(settings)
    run_id = job["runs"][0]["id"]
    for account in ("student-demo", "teacher-alt"):
        outside = login(app, account)
        assert outside.get(f"/api/evaluation-runs/{run_id}").status_code == 403
        assert outside.get(f"/api/jobs/{job['id']}").status_code == 403
        assert outside.get(f"/api/sessions/{sessions['F1']}/timeline").status_code == 403
    teacher.headers.pop("X-CSRF-Token")
    assert teacher.post("/api/auth/logout").json()["code"] == "CSRF"
