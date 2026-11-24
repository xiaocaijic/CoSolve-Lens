import json
from collections import Counter
from uuid import uuid4

import pytest

from backend.app import five_provider
from backend.app.core import Problem, database, dump, get_session, now, uid
from backend.app.evaluation import DEFAULT_CONFIG, recompute
from backend.app.five_provider import (PILOT_SCOPE, clean_assignment, dialogue_chunks, keep_supported_annotations,
                                       read_result, validate_output)
from backend.app.jobs import process_one, schedule_evaluation
from backend.app.rubrics import DIMENSIONS, FIVE_POINT_VERSION, FIVE_VERSION
from scripts.check_studychat_report_quality import apply_corrections
from test_deepseek_studychat import activate, bundle, qualified_bundle
from test_p0 import enqueue_runs, import_demo, login, system
from test_workflows import get_result


def model_result(first="E0001", last="E0003"):
    dimensions = [
        {"dimension": name, "state": "scored", "level": level, "rationale": "引用对话中可观察的具体行为。",
         "event_ids": [first, last]} for name, level in zip(DIMENSIONS, [2, 1, 2, 1, 2], strict=True)
    ]
    return {"dimensions": dimensions,
            "annotations": [{"dimension": "result_evaluation", "type": "positive", "text": "学生明确提出反例。", "event_ids": [last]}],
            "ai_behaviors": {"验证": [{"dimension": "result_evaluation", "text": "学生计划加入反例。", "event_ids": [last]}]},
            "teaching_diagnosis": {"text": "学生已规划验证。", "event_ids": [last]},
            "teaching_recommendations": [{"dimension": name, "text": f"用一个新输入核对预期，目标是 {name}。", "event_ids": [last]}
                                        for name in ("result_evaluation", "information_discernment", "strategy_adjustment")],
            "process_score": 53.3, "limitations": ["没有运行日志。"]}


def structured_model_result():
    result = model_result()
    for dim in result["dimensions"]:
        dim["level"] = (1, 2, 3, 5)[dim["level"]]
    result["process_score"] = 2.6
    old = result.pop("teaching_recommendations")
    result["teacher_recommendations"] = [{"dimension": item["dimension"], "event_ids": item["event_ids"],
        "observation": "学生提出把反例加入检查。", "judgment": "还需记录预期和观察结果。",
        "action": "给出空输入，让学生先写预期。", "follow_up": "交换记录并核对同一个反例。"} for item in old]
    result["student_recommendations"] = [{"dimension": item["dimension"], "event_ids": item["event_ids"],
        "action": "你先写下空输入时的预期值。", "example": "例如预期返回空列表。"} for item in old]
    result["transfer_tasks"] = [{"dimension": "result_evaluation", "title": "换一个边界输入",
        "prompt": "使用只含一个元素的输入，先写预期再运行并解释差异。",
        "success_criteria": "同时记录输入、预期、实际结果和结论。", "event_ids": ["E0003"]}]
    return result


def test_coverage_gate_distinguishes_zero_insufficient_and_not_applicable():
    result = {"dimensions": [
        {"dimension": name, "state": "scored" if i < 3 else ("not_applicable" if i == 3 else "insufficient_evidence"),
         "level": [0, 3, 3, None, None][i]} for i, name in enumerate(DIMENSIONS)], "outcome": {"score": None}}
    recompute(result, DEFAULT_CONFIG | {"rubric_version": FIVE_VERSION})
    assert (result["process_score"], result["process_score_five"]) == (66.7, 3.34)
    for dim, level in zip(result["dimensions"][:3], (5, 4, 3), strict=True):
        dim["level"] = level
    recompute(result, DEFAULT_CONFIG)
    assert (result["process_score"], result["process_score_five"]) == (80, 4)
    result["dimensions"][0]["level"] = 3
    recompute(result, DEFAULT_CONFIG)
    assert (result["process_score"], result["process_score_five"]) == (66.7, 3.33)
    assert result["process_coverage"] == {"scored_count": 3, "total": 5, "minimum": 3}
    result["dimensions"][0].update(state="insufficient_evidence", level=None)
    recompute(result, DEFAULT_CONFIG)
    assert result["process_score"] is None


def test_chunks_preserve_full_order_and_provider_ids_are_validated():
    messages = [{"event_id": "E0001", "role": "student", "text": "首段内容\n" * 200},
                {"event_id": "E0002", "role": "ai", "text": "答复"},
                {"event_id": "E0003", "role": "student", "text": "后半段反例"}]
    parts = [message for segment in dialogue_chunks(messages, 500) for message in segment]
    assert len(parts) > len(messages)
    for message in messages:
        joined = "".join(part["text"] for part in parts if part["event_id"] == message["event_id"])
        assert joined == message["text"]
    assert parts[-1] == messages[-1]
    validated = read_result(dump(model_result()))
    assert validated.ai_behaviors[0].behavior == "verify"
    validate_output(validated, messages)
    invalid = model_result()
    invalid["teaching_recommendations"][0]["event_ids"] = ["E9999"]
    with pytest.raises(Problem, match=".*") as refused:
        validate_output(read_result(dump(invalid)), messages)
    assert refused.value.code == "INVALID_EVIDENCE"
    with pytest.raises(ValueError, match="题目范围外"):
        validate_output(validated, messages, {"E0003"})
    reverse = model_result()
    reverse["ai_behaviors"] = [{"behavior": "adopt", "dimension": "strategy_adjustment", "text": "事件顺序不支持采纳。",
                                "event_ids": ["E0001", "E0002"]}]
    with pytest.raises(ValueError, match="早于"):
        validate_output(read_result(dump(reverse)), messages)
    note = {"dimension": "result_evaluation", "type": "positive", "text": "未支持的学生表述。", "event_ids": ["E0002"]}
    candidate = model_result()
    candidate["annotations"].append(note)
    kept = read_result(dump(candidate))
    assert len(keep_supported_annotations(kept, messages, set())) == 1 and len(kept.annotations) == 1
    note["event_ids"] = ["E9999"]
    with pytest.raises(Problem):
        keep_supported_annotations(read_result(dump(candidate)), messages, set())


def test_public_assignment_attribution_is_removed_before_provider_request():
    original = "# A7\n\n## Attribution\n> Originally developed by Example Person\n\n## Requirements\nPreserve this.\n"
    clean, count = clean_assignment(original, studychat=True)
    assert clean == "# A7\n\n## Requirements\nPreserve this.\n" and count == 1


def test_student_guidance_is_direct_advice_and_has_transfer_task():
    messages = [{"event_id": "E0001", "role": "student", "text": "我先写预期。"},
                {"event_id": "E0003", "role": "student", "text": "我用反例核对。"}]
    result = structured_model_result()
    parsed = read_result(dump(result))
    validate_output(parsed, messages, rubric_version=FIVE_POINT_VERSION)
    result["student_recommendations"][0]["action"] = "你是否可以先写下预期？"
    with pytest.raises(ValueError, match="陈述式动作建议"):
        validate_output(read_result(dump(result)), messages, rubric_version=FIVE_POINT_VERSION)


def test_scoped_five_studychat_reports_and_teacher_audit(system, qualified_bundle, monkeypatch):
    settings, app = system
    teacher, admin, outsider = login(app), login(app, "admin-demo"), login(app, "teacher-alt")
    source = activate(settings, teacher)
    sid = teacher.get("/api/sessions", params={"source_id": source}).json()["items"][0]["id"]
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-test-key")
    assert admin.put("/api/settings/llm", json={"provider": "deepseek", "model": "deepseek-flash",
        "enabled": True, "retention_confirmed": True, "usage_note": "Synthetic scope test"}).status_code == 200
    calls = []

    def completion(profile, key, messages, limit):
        data = json.loads(messages[1]["content"])
        assert [item["event_id"] for item in data["dialogue"]] == [f"E{seq:04d}" for seq in range(1, 9)]
        if limit != 1200:
            assert data["submission_summary"] == {"matched_files": 0}
        for forbidden in ("abc@example.test", "My name is Alex", "assignment_normalized", "archive text should stay local", "0.92"):
            assert forbidden not in messages[1]["content"]
        calls.append(profile["model"])
        body = ({"relevance": "relevant", "text": "学生讨论前缀搜索。", "event_ids": ["E0001"]}
                if limit == 1200 else structured_model_result())
        return dump(body), {"id": "mock-test-response", "model": "deepseek-flash", "usage": {"total_tokens": 100}}

    monkeypatch.setattr(five_provider, "request_completion", completion)
    with database(settings, write=True) as db:
        original = db.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
        admin_user = db.execute("SELECT * FROM users WHERE id='admin-demo'").fetchone()
        ids = [sid, uid(), uid()]
        for clone in ids[1:]:
            db.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
                clone, "dataset", source, original["participant_id"], None, None, original["task_id"],
                "submitted", uid(), original["metadata"], now()))
            for event in db.execute("SELECT * FROM events WHERE session_id=? ORDER BY sequence_no", (sid,)).fetchall():
                db.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?,?)", (
                    uid(), clone, event["sequence_no"], uid(), event["type"], event["occurred_at"],
                    event["timestamp_precision"], event["payload"]))
        artifact_id = uid()
        db.execute("INSERT INTO artifacts VALUES (?,?,?,?,?,?,?)",
                   (artifact_id, sid, "submission_file", 1, "checksum", "archive text should stay local", "text/plain"))
        db.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?,?)",
                   (uid(), sid, 11, uid(), "submission_created", None, "unknown",
                    dump({"artifact_refs": [artifact_id], "provenance": "studychat_assignment_archive"})))
        records = [get_session(db, admin_user, key, "evaluate") for key in ids]
        job = schedule_evaluation(db, admin_user["id"], records, "hybrid", "research", source_id=source, sample_id=uid())
        for key in ids:
            db.execute("INSERT INTO studychat_sample_members VALUES (?,?,?,?,?,?,?,?)", (
                job["id"], key, "deepseek-flash", PILOT_SCOPE, "模拟范围授权", dump({"matched_files": 0}), 10, now()))
        db.execute("UPDATE evaluation_runs SET status='cancelled' WHERE job_id=?", (job["id"],))
        db.execute("UPDATE jobs SET status='cancelled' WHERE id=?", (job["id"],))
    scope = {"source_id": source, "session_ids": ids, "model": "deepseek-flash", "max_count": 3}
    check = admin.post("/api/analysis/inspect", json=scope).json()
    response = admin.post("/api/analysis/start", json=scope | {
        "checked_scope": check["checked_scope"], "request_key": str(uuid4()), "confirm_model_scope": True})
    assert response.status_code == 202, response.text
    job = response.json()
    assert process_one(settings)
    finished = admin.get("/api/jobs/" + job["id"]).json()
    assert finished["succeeded_count"] == 3 and calls == ["deepseek-flash"] * 6
    run = next(item["id"] for item in finished["runs"] if item["session_id"] == sid)
    report = get_result(teacher, run)
    assert report["rubric_version"] == FIVE_POINT_VERSION and report["provider"] == "deepseek" and not report["degraded"]
    assert (report["process_score"], report["process_score_five"], report["outcome"]["score"]) == (52, 2.6, None)
    assert report["dimensions"][3]["state"] == "scored"
    assert report["provider_call"]["dialogue_messages"] == 8
    detail = teacher.get(f"/api/sessions/{sid}").json()
    assert detail["studychat"]["source_outcome_score"] == 0.92 and detail["studychat"]["source_outcome_percent"] == 92
    assert detail["studychat"]["message_count"] == 10
    assert (report["source_outcome_score"], report["system_outcome_score"], report["outcome_basis"]) == (0.92, None, "source_grade")
    assert report["diagnosis"]["label"] == "过程需加强、成果达标"
    page = teacher.get(f"/api/sessions/{sid}/dialogue", params={"page_size": 2, "event_id": report["annotations"][0]["evidence_refs"][0]["event_id"]})
    assert page.status_code == 200 and page.json()["page"] == 2
    assert outsider.get(f"/api/sessions/{sid}/dialogue").status_code == 403
    assert outsider.get(f"/api/evaluation-runs/{run}").status_code == 403
    body = {"decision": "amend", "reason": "已经核对对话。", "expected_report_version": report["id"],
            "dimensions": [{"dimension": "result_evaluation", "state": "scored", "level": 4,
                            "rationale": "提出了可以检验的反例。", "event_ids": report["dimensions"][3]["event_ids"]}]}
    outside = next(event_id for event_id, position in report["evidence_turns"].items() if position["sequence"] == 9)
    refused = teacher.post(f"/api/evaluation-runs/{run}/reviews", json=body | {
        "dimensions": [body["dimensions"][0] | {"event_ids": [outside]}]})
    assert refused.status_code == 422 and refused.json()["code"] == "STUDYCHAT_EVIDENCE_SCOPE"
    amended = teacher.post(f"/api/evaluation-runs/{run}/reviews", json=body)
    assert amended.status_code == 201 and amended.json()["process_score"] == 60
    assert get_result(teacher, run, report["id"])["process_score"] == 52
    audit = teacher.get(f"/api/evaluation-runs/{run}/versions").json()["items"][0]
    assert audit["changes"][0]["target"] == "dimension:result_evaluation"
    assert teacher.post(f"/api/evaluation-runs/{run}/reviews", json=body).status_code == 409
    inputs_path = f"/api/evaluation-runs/{run}/local-outcome-inputs?report_id={amended.json()['id']}"
    inputs = teacher.get(inputs_path)
    assert inputs.status_code == 200 and [item["id"] for item in inputs.json()["artifacts"]] == [artifact_id]
    assert outsider.get(inputs_path).status_code == 403
    local_items = [{"item_id": item["item_id"], "score": score, "reason": "按归档材料核对。",
                    "artifact_ids": [artifact_id]} for item, score in zip(inputs.json()["criteria"], [100, 60, 70], strict=True)]
    review_path = f"/api/evaluation-runs/{run}/reviews"
    local_body = {"decision": "amend", "reason": "按照本地成果量规检查提交。", "expected_report_version": amended.json()["id"]}
    unknown = local_items[:2] + [local_items[2] | {"artifact_ids": [uid()]}]
    assert teacher.post(review_path, json=local_body | {"local_outcome": unknown}).json()["code"] == "LOCAL_OUTCOME_EVIDENCE"
    partial = teacher.post(review_path, json=local_body | {"local_outcome": local_items[:2] + [local_items[2] | {"score": None}]}).json()
    assert (partial["system_outcome_score"], partial["outcome_basis"]) == (None, "source_grade")
    saved = teacher.post(review_path, json=local_body | {"local_outcome": local_items, "expected_report_version": partial["id"]})
    assert saved.status_code == 201, saved.text
    local = get_result(teacher, run)
    assert (local["system_outcome_score"], local["source_outcome_percent"], local["outcome_basis"]) == (73, 92, "local_review")
    assert local["diagnosis"]["label"] == "过程与成果均需支持"
    assert local["outcome"]["rubric_version"] == "local-outcome-1.0"
    assert get_result(teacher, run, report["id"])["system_outcome_score"] is None
    assert teacher.get(f"/api/evaluation-runs/{run}/versions").json()["items"][0]["changes"][0]["target"] == "outcome"
    assert teacher.post(f"/api/datasets/{source}/studychat-pilot", json={"method": "hybrid"}).status_code == 409
    assert len(calls) == 6
    second_run = next(item["id"] for item in finished["runs"] if item["session_id"] != sid)
    second = get_result(teacher, second_run)
    with database(settings, write=True) as db:
        quality = apply_corrections(db, job["id"], {"report_id": second["id"], "summary": "独立证据核查",
            "edits": [{"path": ["limitations", 0], "evidence": [3], "reason": "明确记录覆盖范围。", "value": "只有对话证据。"}]})
    checked = get_result(teacher, second_run)
    assert checked["id"] == quality["report_id"] and checked["parent_id"] == second["id"]
    assert checked["kind"] == "system" and "review" not in checked
    assert teacher.get(f"/api/evaluation-runs/{second_run}/versions").json()["items"][0]["changes"][0]["target"] == "limitations.0"


def test_five_fixture_conflict_and_frozen_legacy_report(system):
    settings, app = system
    client = login(app)
    source, sessions = import_demo(settings, client)
    sid = sessions["F5"]
    with database(settings, write=True) as db:
        from backend.app.evaluation import initial_config

        old = initial_config(db, DEFAULT_CONFIG | {"rubric_version": "bars-1.2", "prompt_version": "prompt-1", "ruleset_version": "rules-1"})
        user = db.execute("SELECT * FROM users WHERE id='teacher-demo'").fetchone()
        frozen = schedule_evaluation(db, user["id"], [get_session(db, user, sid)], "demo_conflict", "demo",
                                     source_id=source, config_version=old)["id"]
    process_one(settings)
    old_run = client.get(f"/api/jobs/{frozen}").json()["runs"][0]["id"]
    legacy = get_result(client, old_run)
    assert legacy["legacy_rubric"] and len(legacy["dimensions"]) == 4
    new_run = enqueue_runs(client, source, [sid], "demo_conflict")["runs"][0]["id"]
    process_one(settings)
    current = get_result(client, new_run)
    assert current["rubric_version"] == FIVE_POINT_VERSION
    assert Counter(item["dimension"] for item in current["dimensions"]) == Counter(DIMENSIONS.keys())
    conflict = current["conflicts"][0]
    assert conflict["dimension"] == "result_evaluation" and conflict["state"] == "pending_review"
    resolved = client.post(f"/api/evaluation-runs/{new_run}/reviews", json={
        "expected_report_version": current["id"], "decision": "amend", "reason": "根据最终测试与提交快照复核。",
        "conflict_resolution": "accept_rule", "adjudication_event_ids": conflict["rule_event_ids"],
    })
    assert resolved.status_code == 201 and resolved.json()["conflicts"][0]["state"] == "resolved"
    assert get_result(client, old_run, legacy["id"])["dimensions"] == legacy["dimensions"]
    historical = client.get(f"/api/datasets/{source}/results", params={"rubric": "legacy", "dimension": "verification"}).json()
    assert historical["total"] == 1 and historical["items"][0]["id"] == legacy["id"]
