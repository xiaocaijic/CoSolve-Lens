"""Apply evidence corrections reviewed by an independent agent to this pilot only."""
import argparse
import json

from backend.app.core import Settings, database, dump, now
from backend.app.evaluation import recompute, store_report
from backend.app.rubrics import is_five_dimension


FIELDS = {"dimensions": {"level", "rationale", "event_ids"}, "annotations": {"text"},
          "teaching_diagnosis": {"text", "event_ids"}, "teaching_recommendations": {"text", "event_ids"},
          "teacher_recommendations": {"observation", "judgment", "action", "follow_up", "event_ids"},
          "student_recommendations": {"action", "example", "event_ids"}}


def apply_corrections(db, job_id, entry):
    report_id = entry["report_id"]
    report_row = db.execute("""SELECT r.body,r.run_id FROM report_versions r
        JOIN effective_reports er ON er.report_id=r.id JOIN evaluation_runs e ON e.id=r.run_id
        JOIN studychat_sample_members m ON m.job_id=e.job_id AND m.session_id=e.session_id
        WHERE r.id=? AND e.job_id=? AND r.kind='system'""", (report_id, job_id)).fetchone()
    if not report_row:
        raise SystemExit("质量核查仅适用于指定受限批次的当前系统报告。")
    report = json.loads(report_row["body"])
    run = db.execute("SELECT * FROM evaluation_runs WHERE id=?", (report_row["run_id"],)).fetchone()
    if not is_five_dimension(report["rubric_version"]) or report["provider"] != "deepseek" or report["degraded"]:
        raise SystemExit("质量核查的报告量规或模型不匹配。")
    events = {row["sequence_no"]: row["id"] for row in db.execute(
        "SELECT id,sequence_no FROM events WHERE session_id=? AND sequence_no<=? AND type IN ('chat_user_message','chat_ai_message')",
        (run["session_id"], run["event_cutoff"]))}
    audit = []
    for edit in entry["edits"]:
        path = edit["path"]
        remove_behavior = len(path) == 2 and path[0] == "ai_behaviors" and type(path[1]) is int and edit.get("remove") is True
        valid_path = (len(path) == 2 and path[0] == "limitations" and type(path[1]) is int) or (
            len(path) in (2, 3) and path[-1] in FIELDS.get(path[0], set())) or remove_behavior
        if not valid_path or not edit["evidence"] or not set(edit["evidence"]) <= events.keys():
            raise SystemExit("证据质检修改的字段或事件不合法。")
        container = report
        for key in path[:-1]:
            container = container[key]
        key = path[-1]
        before = container[key]
        if "expected" in edit and edit["expected"] != before:
            raise SystemExit("质量核查字段已变更。请使用实际报告重新核对。")
        refs = [events[sequence] for sequence in edit["evidence"]]
        excluded = set(report.get("out_of_task_event_ids", [])) | set(report.get("studychat_quality", {}).get("excluded_event_ids", []))
        if set(refs) & excluded:
            raise SystemExit("题目范围外事件不能支持证据质检修改。")
        value = None if remove_behavior else refs if key == "event_ids" else edit["value"]
        if remove_behavior:
            del container[key]
        else:
            container[key] = value
        audit.append({"target": ".".join(map(str, path)), "before": before, "after": value,
                      "event_ids": refs, "reason": edit["reason"]})
    report["teaching_suggestions"] = [item["text"] for item in report["teaching_recommendations"]]
    prior = report.get("system_quality_review", {})
    initial = prior.get("system_parent_id", report_id)
    report["system_quality_review"] = {"created_at": now(), "reason": entry["summary"], "system_parent_id": initial,
                                       "changes": [*prior.get("changes", []), *audit]}
    config = json.loads(db.execute("SELECT settings FROM evaluation_configs WHERE id=?", (run["config_version"],)).fetchone()[0])
    recompute(report, config)
    result = store_report(db, run, report, parent_id=report_id, system_correction=True)
    return {"run_id": run["id"], "system_parent_id": report_id, "report_id": result["id"], "process_score": result["process_score"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corrections", type=argparse.FileType("r", encoding="utf-8-sig"))
    args = parser.parse_args()
    changes = json.load(args.corrections)
    with database(Settings(), write=True) as db:
        results = [apply_corrections(db, changes["job_id"], entry) for entry in changes["reports"]]
    print(dump(results))


if __name__ == "__main__":
    main()
