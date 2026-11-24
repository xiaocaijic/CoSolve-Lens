import csv
import io
import json
import time
from typing import Literal

from fastapi import Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .api import router
from .core import Problem, database, digest, dump, get_session, get_source, require_role, row_or_404, uid
from .http import Page, PageSize, User, page_result
from .jobs import enqueue, public_job
from .outcomes import report_outcomes
from .evaluation import five_point_score
from .rubrics import is_five_dimension
from .studychat_policy import is_snapshot

PUBLIC_SCORE_KEYS = {"assignment_score", "assignment_normalized", "e1_normalized", "e2_normalized",
                     "e3_normalized", "llm_normalized", "no_llm_normalized"}


def external_grades(row):
    source = json.loads(row["session_metadata"]).get("external_scores", {})
    return {key: source[key] for key in PUBLIC_SCORE_KEYS if key in source}


class ExportRequest(BaseModel):
    source_id: str
    format: Literal["json", "csv"] = "json"
    include_demo: bool = False
    acknowledge_scope: bool = False


def safe_report(report):
    actual_method = "rules" if report["provider"] == "rules" else report["method"]
    return {
        "id": report["id"], "version": report["version"], "kind": report["kind"], "method": report["method"],
        "process_score": report["process_score"],
        "process_score_five": report.get("process_score_five", five_point_score(report["process_score"])),
        "outcome_score": report.get("display_outcome_score", report["outcome"]["score"]),
        "system_outcome_score": report["outcome"]["score"], "source_outcome_score": report.get("source_outcome_score"),
        "source_outcome_percent": report.get("source_outcome_percent"), "outcome_basis": report.get("outcome_basis"),
        "local_outcome_rubric_version": (report.get("local_outcome") or report["outcome"]).get("rubric_version"),
        "diagnosis": report["diagnosis"]["label"], "degraded": report["degraded"], "demo_only": report["demo_only"],
        "rubric_version": report["rubric_version"], "legacy_rubric": not is_five_dimension(report["rubric_version"]),
        "process_coverage": report.get("process_coverage"),
        "ai_behaviors": [{"dimension": item["dimension"], "behavior": item["behavior"], "event_ids": item["event_ids"]}
                         for item in report.get("ai_behaviors", [])],
        "config_version": report["config_version"], "purpose": report["purpose"],
        "review_status": report.get("review", {}).get("decision"), "actual_method": actual_method, "dimensions": {
            item["dimension"]: {"state": item["state"], "level": item["level"], "reason": item["reason"]}
            for item in report["dimensions"]
        },
    }


def verified_rows(db, user, source_id=None, demo=False, permission="view"):
    rows = db.execute(
        """SELECT r.body,r.session_id,r.method,r.run_id,s.source_type,s.source_id,s.owner_user_id,s.task_id,
                  p.external_key_hash AS participant,s.metadata AS session_metadata,d.adapter,d.source_identity
           FROM effective_reports er JOIN report_versions r ON r.id=er.report_id
           JOIN sessions s ON s.id=r.session_id LEFT JOIN participants p ON p.id=s.participant_id
           LEFT JOIN data_sources d ON d.id=s.source_id
           WHERE s.source_type='dataset' AND (? IS NULL OR s.source_id=?) ORDER BY r.created_at DESC""",
        (source_id, source_id),
    )
    for row in rows:
        body = json.loads(row["body"])
        if body["demo_only"] != demo:
            continue
        try:
            get_session(db, user, row["session_id"], permission)
        except Problem as exc:
            if exc.status == 403:
                continue
            raise
        yield row, report_outcomes(db, body, json.loads(row["session_metadata"]), is_snapshot(row))


def safe_events(db, sid, demo):
    for row in db.execute("SELECT sequence_no,type,timestamp_precision,payload FROM events WHERE session_id=? ORDER BY sequence_no", (sid,)):
        payload = json.loads(row["payload"])
        if payload.get("synthetic") and not demo:
            continue
        yield {key: row[key] for key in ("sequence_no", "type", "timestamp_precision")} | {
            "measurements": {key: value for key, value in payload.items() if key in (
                "version", "artifact_version", "execution_mode", "verified", "synthetic", "exit_code", "passed", "failed"
            ) and (isinstance(value, (bool, int)) or value in ("sandbox", "fixture_simulation", "external_unknown"))}
        }


def execute_export(db, job, inputs):
    user = db.execute("SELECT * FROM users WHERE id=?", (job["owner_user_id"],)).fetchone()
    source = get_source(db, user, job["source_id"], "export")
    demo = bool(source["demo"])
    data = []
    for row, report in verified_rows(db, user, source["id"], demo, "export"):
        saved = safe_report(report)
        participant = row["participant"]
        grades = external_grades(row) if not is_snapshot(row) or report["source_outcome_score"] is not None else {}
        if inputs["validation"]:
            percentile = int(digest(participant)[:8], 16) % 100
            split = "train" if percentile < 60 else "development" if percentile < 80 else "test"
            saved.update(split=split, manual_dimensions={dim: None for dim in saved["dimensions"]},
                         manual_review_reason="")
        data.append({"session_id": row["session_id"], "participant": participant, "report": saved,
                     "external_scores": grades, "source_outcome_score": saved["source_outcome_score"],
                     "system_outcome_score": saved["system_outcome_score"], "outcome_basis": saved["outcome_basis"],
                     "events": list(safe_events(db, row["session_id"], demo))})
    if inputs["format"] == "json":
        content = dump({"source_id": source["id"], "demo_only": demo, "rows": data,
                        "scope": "默认导出省略对话、代码、附件原文及姓名和原始学生标识。"})
    else:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["participant", "session_id", "method", "version", "process_score", "process_score_five", "outcome_score",
                         "diagnosis", "review_status", "degraded", "demo_only", "dimensions_json", "events_json",
                         "external_scores_json", "rubric_version", "legacy_rubric", "process_coverage_json",
                         "source_outcome_score", "source_outcome_percent", "system_outcome_score", "outcome_basis",
                         "local_outcome_rubric_version", "ai_behaviors_json"])
        for item in data:
            report = item["report"]
            writer.writerow([item["participant"], item["session_id"], report["method"], report["version"],
                             report["process_score"], report["process_score_five"], report["outcome_score"], report["diagnosis"], report["review_status"],
                             report["degraded"], report["demo_only"], dump(report["dimensions"]), dump(item["events"]),
                             dump(item["external_scores"]), report["rubric_version"], report["legacy_rubric"],
                             dump(report["process_coverage"]), item["source_outcome_score"], report["source_outcome_percent"],
                             item["system_outcome_score"], item["outcome_basis"], report["local_outcome_rubric_version"],
                             dump(report["ai_behaviors"])])
        content = output.getvalue()
    export_id = uid()
    db.execute("INSERT INTO exports VALUES (?,?,?,?,?,?,?)",
               (export_id, job["id"], user["id"], source["id"], inputs["format"], content, time.time() + 3600))
    return {"export_id": export_id, "rows": len(data)}


def queue_export(db, user, body, validation):
    source = get_source(db, user, body.source_id, "export")
    if not body.acknowledge_scope:
        raise Problem(422, "EXPORT_SCOPE", "请确认导出的字段范围。")
    if source["demo"] and (validation or not body.include_demo):
        raise Problem(422, "DEMO_EXPORT_SCOPE", "演示数据只能通过明确启用的演示导出下载。")
    if validation and body.format != "json":
        raise Problem(422, "VALIDATION_FORMAT", "验证样本使用 JSON 格式。")
    report_ids = [row["report_id"] for row in db.execute(
        """SELECT er.report_id FROM effective_reports er JOIN sessions s ON s.id=er.session_id
           WHERE s.source_id=? ORDER BY er.report_id""", (source["id"],)
    )]
    job, _ = enqueue(db, user["id"], "export", {"source": source["id"], "format": body.format,
                     "validation": validation, "revision": digest(dump(report_ids)),
                     "window": int(time.time() // 3600)}, 1, source_id=source["id"])
    return public_job(db, job)


@router.post("/exports", status_code=202)
def export_data(request: Request, user: User, body: ExportRequest):
    require_role(user, "teacher", "admin")
    with database(request.app.state.settings, write=True) as db:
        return queue_export(db, user, body, False)


@router.post("/analytics/validation-exports", status_code=202)
def validation_export(request: Request, user: User, body: ExportRequest):
    require_role(user, "teacher", "admin")
    with database(request.app.state.settings, write=True) as db:
        return queue_export(db, user, body, True)


@router.get("/exports/{export_id}/download")
def download(request: Request, user: User, export_id: str):
    with database(request.app.state.settings) as db:
        entry = row_or_404(db.execute("SELECT * FROM exports WHERE id=?", (export_id,)).fetchone())
        get_source(db, user, entry["source_id"], "export")
        if entry["expires_at"] < time.time():
            raise Problem(410, "EXPORT_EXPIRED", "下载已过期，请重新生成。")
        extension = entry["format"]
        return Response(entry["content"], media_type="application/json" if extension == "json" else "text/csv",
                        headers={"Content-Disposition": f'attachment; filename="assessment-export.{extension}"'})


@router.get("/analytics/overview")
def overview(request: Request, user: User, scope: Literal["research", "demo"] = "research", source_id: str | None = None):
    require_role(user, "teacher", "admin")
    with database(request.app.state.settings) as db:
        if source_id:
            get_source(db, user, source_id)
        data = list(verified_rows(db, user, source_id=source_id, demo=scope == "demo"))
    dimensions, dimensions_by_rubric, methods, outcomes = {}, {}, {}, {}
    for _, report in data:
        method = safe_report(report)["actual_method"]
        methods[method] = methods.get(method, 0) + 1
        label = report["diagnosis"]["label"] or "暂不形成二维诊断"
        outcomes[label] = outcomes.get(label, 0) + 1
        rubric_dimensions = dimensions_by_rubric.setdefault(report["rubric_version"], {})
        for dim in report["dimensions"]:
            counts = dimensions.setdefault(dim["dimension"], {})
            native_counts = rubric_dimensions.setdefault(dim["dimension"], {})
            key = str(dim["level"]) if dim["state"] == "scored" else dim["state"]
            counts[key] = counts.get(key, 0) + 1
            native_counts[key] = native_counts.get(key, 0) + 1
    external = {}
    for row, report in data:
        if is_snapshot(row) and report["source_outcome_score"] is None:
            continue
        grades = json.loads(row["session_metadata"]).get("external_scores", {})
        if "assignment_normalized" in grades:
            external[row["session_id"]] = grades["assignment_normalized"]
    return {"scope": scope, "report_count": len(data), "sample_count": len({row["session_id"] for row, _ in data}),
            "scorable_count": sum(report["process_score"] is not None for _, report in data),
            "insufficient_count": sum(any(dim["state"] == "insufficient_evidence" for dim in report["dimensions"])
                                      for _, report in data),
            "dimensions": dimensions, "dimensions_by_rubric": dimensions_by_rubric, "methods": methods, "diagnoses": outcomes,
            "process": [report["process_score"] for _, report in data if report["process_score"] is not None],
            "outcome": [report["outcome"]["score"] for _, report in data if report["outcome"]["score"] is not None],
            "external_assignments": list(external.values())}


@router.get("/analytics/disagreements")
def disagreements(request: Request, user: User, page: Page = 1, page_size: PageSize = 20,
                  scope: Literal["research", "demo"] = "research", source_id: str | None = None):
    require_role(user, "teacher", "admin")
    groups = {}
    with database(request.app.state.settings) as db:
        if source_id:
            get_source(db, user, source_id)
        for row, report in verified_rows(db, user, source_id=source_id, demo=scope == "demo"):
            grades = external_grades(row) if not is_snapshot(row) or report["source_outcome_score"] is not None else {}
            summarized = safe_report(report) | {"run_id": row["run_id"], "external_scores": grades}
            if report["kind"] == "reviewed":
                first = db.execute("SELECT body FROM report_versions WHERE run_id=? AND version=1", (row["run_id"],)).fetchone()
                summarized["system"] = safe_report(json.loads(first["body"]))
            methods = groups.setdefault((row["session_id"], report["rubric_version"]), {})
            method = summarized["actual_method"]
            previous = methods.get(method)
            if not previous or (previous["method"] != method and summarized["method"] == method):
                methods[method] = summarized
    items = []
    for (session_id, rubric_version), methods in groups.items():
        reports = list(methods.values())
        alternatives = {dump(report["dimensions"]) for report in reports}
        changed = any(report.get("system") and report["dimensions"] != report["system"]["dimensions"] for report in reports)
        if len(reports) > 1 or changed:
            items.append({"session_id": session_id, "method_disagreement": len(alternatives) > 1,
                          "teacher_disagreement": changed, "reports": reports})
    return page_result(items, page, page_size)
