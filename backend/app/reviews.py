import copy
import json
from typing import Literal

from fastapi import Request
from pydantic import BaseModel, Field

from .api import router
from .core import Problem, database, dump, get_session, now, require_role, row_or_404, uid
from .evaluation import Dimension, annotate, event_payloads, recompute, require_event_ids, store_report, suggestions
from .http import Page, PageSize, User, get_report, get_run, page_result
from .jobs import public_job, schedule_evaluation
from .local_outcomes import CRITERIA, RUBRIC_VERSION, LocalOutcomeItem, assess_outcome, outcome_artifacts
from .outcomes import outcome_fields
from .rubrics import DimensionName, is_five_dimension, level_bounds


class ReviewRequest(BaseModel):
    expected_report_version: str
    decision: Literal["confirm", "amend", "rerun"]
    reason: str = Field(min_length=1, max_length=3000)
    dimensions: list[Dimension] = Field(default_factory=list, max_length=5)
    annotations: list["AnnotationChange"] | None = Field(default=None, max_length=40)
    teaching_suggestions: list[str] | None = Field(default=None, max_length=12)
    conflict_resolution: Literal["accept_rule", "adjust_judgment"] | None = None
    adjudication_event_ids: list[str] = Field(default_factory=list, max_length=30)
    local_outcome: list[LocalOutcomeItem] | None = Field(default=None, max_length=3)
    release: bool = True


class AnnotationChange(BaseModel):
    dimension: DimensionName
    type: Literal["positive", "issue", "turning_point", "insufficient_evidence"]
    text: str = Field(min_length=1, max_length=2000)
    event_ids: list[str] = Field(min_length=1, max_length=30)


class Expected(BaseModel):
    expected_report_version: str


def current_report(db, user, run, expected):
    entry = db.execute(
        """SELECT rv.id,rv.run_id FROM effective_reports er JOIN report_versions rv ON rv.id=er.report_id
           WHERE er.session_id=? AND er.method=? AND er.purpose=?""", (run["session_id"], run["method"], run["purpose"])
    ).fetchone()
    if not entry or entry["id"] != expected or entry["run_id"] != run["id"]:
        raise Problem(409, "REPORT_VERSION", "报告已有新版本，请刷新后复核。")
    return get_report(db, user, entry["id"], "review")


def safe_text(text):
    if not text.strip():
        raise Problem(422, "REVIEW_TEXT", "请填写清楚的复核说明。")
    if any(phrase in text for phrase in ("认定作弊", "确认作弊", "确认AI代工", "确认 AI 代工", "判定学术不端")):
        raise Problem(422, "REVIEW_TEXT", "请描述可观察证据和教学建议。")
    return text.strip()


def tracked_review(db, run, report, user, decision, reason, changes):
    audit_id = uid()
    db.execute("INSERT INTO reviews VALUES (?,?,?,?,?,?,?)",
               (audit_id, run["id"], report["id"], user["id"], decision, reason, now()))
    db.executemany("INSERT INTO review_changes VALUES (?,?,?,?)", [
        (audit_id, key, dump(before), dump(after)) for key, (before, after) in changes.items()
    ])


@router.get("/reviews/queue")
def review_queue(request: Request, user: User, page: Page = 1, page_size: PageSize = 20,
                 state: Literal["unreviewed", "conflict", "low_confidence", "insufficient", "reviewed", "all"] = "unreviewed",
                 sort: Literal["newest", "oldest"] = "newest"):
    require_role(user, "teacher", "admin")
    with database(request.app.state.settings) as db:
        order = "DESC" if sort == "newest" else "ASC"
        rows = db.execute(f"""SELECT rv.* FROM effective_reports er JOIN report_versions rv ON rv.id=er.report_id
                              ORDER BY rv.created_at {order}""")
        items = []
        for row in rows:
            try:
                session = get_session(db, user, row["session_id"], "review")
            except Problem as exc:
                if exc.status == 403:
                    continue
                raise
            body = json.loads(row["body"])
            reviewed = row["kind"] == "reviewed"
            conflict = any(item["state"] == "pending_review" for item in body["conflicts"]) or (
                not reviewed and bool(body["raw_rule_facts"].get("conflicting_execution_event_ids"))
            )
            insufficient = any(dim["state"] == "insufficient_evidence" for dim in body["dimensions"])
            low = any(item["confidence"] < 0.7 for item in body["annotations"])
            if not {"all": True, "unreviewed": not reviewed, "reviewed": reviewed, "conflict": conflict,
                    "insufficient": insufficient, "low_confidence": low}[state]:
                continue
            task = db.execute("SELECT title FROM task_definitions WHERE id=?", (session["task_id"],)).fetchone()
            items.append({"id": row["id"], "run_id": row["run_id"], "task": task["title"], "method": row["method"],
                          "source_type": session["source_type"], "reviewed": reviewed, "conflict": conflict,
                          "insufficient": insufficient, "demo_only": body["demo_only"]})
        return page_result(items, page, page_size)


@router.get("/evaluation-runs/{run_id}/local-outcome-inputs")
def local_outcome_inputs(request: Request, user: User, run_id: str, report_id: str):
    require_role(user, "teacher", "admin")
    with database(request.app.state.settings) as db:
        run = get_run(db, user, run_id, "review")
        report = current_report(db, user, run, report_id)
        events = event_payloads(db, run["session_id"], run["event_cutoff"])
        artifacts, _ = outcome_artifacts(db, run["session_id"], events, report)
        return {"rubric_version": RUBRIC_VERSION, "criteria": CRITERIA, "artifacts": list(artifacts.values())}


@router.post("/evaluation-runs/{run_id}/reviews", status_code=201)
def review(request: Request, user: User, run_id: str, body: ReviewRequest):
    require_role(user, "teacher", "admin")
    reason = safe_text(body.reason)
    with database(request.app.state.settings, write=True) as db:
        run = get_run(db, user, run_id, "review")
        original = current_report(db, user, run, body.expected_report_version)
        report = copy.deepcopy(original)
        for key in ("effective", "released_to_student", "can_review", "parent_run_id", "outcome_display_projection", "evidence_turns"):
            report.pop(key, None)
        if report["legacy_rubric"]:
            report.pop("display_outcome_score", None)
        events = {item["id"]: item for item in event_payloads(db, run["session_id"], run["event_cutoff"])}
        previous = {item["dimension"]: item for item in original["dimensions"]}
        edits = {item.dimension: item.model_dump() for item in body.dimensions}
        if len(edits) != len(body.dimensions):
            raise Problem(422, "REVIEW_DIMENSIONS", "同一维度只能修改一次。")
        if not set(edits) <= set(previous) or any(item.dimension not in previous for item in body.annotations or []):
            raise Problem(422, "REVIEW_DIMENSIONS", "复核维度必须与当前报告量规一致。")
        if body.decision != "amend" and (edits or body.annotations is not None or body.teaching_suggestions is not None
                                        or body.conflict_resolution or body.local_outcome is not None):
            raise Problem(422, "REVIEW_DECISION", "修改结果或裁决冲突时请选择修改后确认。")
        changes = {}
        pending = [item for item in report["conflicts"] if item["state"] == "pending_review"]
        if pending and body.decision != "rerun":
            if not body.conflict_resolution or not body.adjudication_event_ids:
                raise Problem(422, "CONFLICT_PENDING", "请选择冲突裁决并附上证据，或退回重评。")
            require_event_ids(body.adjudication_event_ids, events)
            for conflict in pending:
                name = conflict["dimension"]
                if body.conflict_resolution == "accept_rule":
                    if not set(conflict["rule_event_ids"]) <= set(body.adjudication_event_ids):
                        raise Problem(422, "CONFLICT_EVIDENCE", "请引用规则事实对应的执行事件与提交事件。")
                    rule_result = copy.deepcopy(conflict["rule_result"])
                    if run["method"] == "demo_conflict":
                        rule_result["event_ids"] = list(dict.fromkeys([
                            *conflict["candidate"]["event_ids"], *rule_result["event_ids"]
                        ]))
                    edits[name] = rule_result
                elif name not in edits:
                    raise Problem(422, "CONFLICT_PENDING", "请填写受冲突影响维度的修改结果。")
                conflict.update(state="resolved", resolution=body.conflict_resolution,
                                adjudication_event_ids=body.adjudication_event_ids, reviewer_id=user["id"])
            changes["conflicts"] = (original["conflicts"], report["conflicts"])
        draft = False
        for name, edit in edits.items():
            require_event_ids(edit["event_ids"], events)
            edit["rationale"] = safe_text(edit["rationale"])
            if edit["level"] in level_bounds(report["rubric_version"]) and len(set(edit["event_ids"])) < 2:
                prior = previous[name]
                if (prior["level"], prior["event_ids"]) != (edit["level"], edit["event_ids"]):
                    draft = True
            changes["dimension:" + name] = (previous[name], edit)
        report["dimensions"] = [edits.get(name, previous[name]) for name in previous]
        report["annotations"] = [item for item in report["annotations"]
                                 if item["dimension"] not in edits or item["type"] == "rule_llm_conflict"]
        report["annotations"].extend(annotate(edit, events, "teacher") for edit in edits.values() if edit["event_ids"])
        if body.annotations is not None:
            report["annotations"] = [item for item in report["annotations"] if item["source"] != "teacher"]
            for annotation in body.annotations:
                require_event_ids(annotation.event_ids, events)
                report["annotations"].append(annotate({"dimension": annotation.dimension, "level": None,
                                                       "rationale": safe_text(annotation.text), "event_ids": annotation.event_ids},
                                                      events, "teacher", kind=annotation.type))
            changes["annotations"] = (original["annotations"], report["annotations"])
        if body.teaching_suggestions is not None:
            if any(len(text) > 1500 for text in body.teaching_suggestions):
                raise Problem(422, "REVIEW_TEXT", "教学建议过长。")
            report["teaching_suggestions"] = [safe_text(text) for text in body.teaching_suggestions]
            if report.get("teaching_recommendations"):
                changes["teaching_recommendations"] = (original["teaching_recommendations"], [])
                report["teaching_recommendations"] = []
            if report.get("teacher_recommendations"):
                changes["teacher_recommendations"] = (original["teacher_recommendations"], [])
                report["teacher_recommendations"] = []
            report["guidance_basis"] = "teacher_edit"
        elif pending and body.conflict_resolution:
            if not is_five_dimension(report["rubric_version"]):
                report["teaching_suggestions"] = suggestions(report["dimensions"])
        if original["teaching_suggestions"] != report["teaching_suggestions"]:
            changes["teaching_suggestions"] = (original["teaching_suggestions"], report["teaching_suggestions"])
        if body.local_outcome is not None:
            artifacts, submission_id = outcome_artifacts(db, run["session_id"], events.values(), original)
            local = assess_outcome(body.local_outcome, artifacts, submission_id, safe_text)
            automatic = original.get("automatic_outcome")
            if automatic is None and original["outcome"].get("basis") != "local_review" and original["outcome"]["score"] is not None:
                automatic = original["outcome"]
            if automatic is not None:
                report["automatic_outcome"] = automatic
            report["local_outcome"] = local
            report["outcome"] = automatic if not local["complete"] and automatic is not None else local
            changes["outcome"] = (original.get("local_outcome") or original["outcome"], local)
            old_warning = "StudyChat 记录反映对话中可观察的协同过程。没有可核验的运行日志或独立成果量规结果。"
            report["warnings"] = [
                "StudyChat 过程评价依据对话中可观察的行为。归档缺少可核验的运行日志。" if warning == old_warning else warning
                for warning in report["warnings"]
            ]
            source = original["source_outcome_score"]
            outcome = report["outcome"]
            report.update(outcome_fields(outcome["score"], source, studychat=source is not None,
                                         local_review=outcome.get("basis") == "local_review"))
        config = json.loads(db.execute("SELECT settings FROM evaluation_configs WHERE id=?", (run["config_version"],)).fetchone()["settings"])
        recompute(report, config)
        report["review"] = {"decision": body.decision, "reason": reason, "reviewer": user["display_name"],
                            "created_at": now(), "draft": draft}
        release = body.release and run["purpose"] in {"demo", "student_feedback"} and not draft and body.decision != "rerun"
        derived = store_report(db, run, report, parent_id=original["id"], effective=not draft, release=release)
        tracked_review(db, run, derived, user, body.decision, reason, changes)
        if body.decision == "rerun":
            session = get_session(db, user, run["session_id"], "review")
            job = schedule_evaluation(db, user["id"], [session], run["method"], run["purpose"],
                                      source_id=session["source_id"], parent=run_id)
            derived["rerun_job_id"] = job["id"]
        return {**derived, "effective": not draft, "released_to_student": release}


@router.post("/evaluation-runs/{run_id}/rerun", status_code=202)
def rerun(request: Request, user: User, run_id: str, body: Expected):
    require_role(user, "teacher", "admin")
    with database(request.app.state.settings, write=True) as db:
        run = get_run(db, user, run_id, "evaluate")
        current_report(db, user, run, body.expected_report_version)
        session = get_session(db, user, run["session_id"], "evaluate")
        job = schedule_evaluation(db, user["id"], [session], run["method"], run["purpose"],
                                  source_id=session["source_id"], parent=run_id)
        return public_job(db, job)


@router.get("/evaluation-runs/{run_id}/versions")
def versions(request: Request, user: User, run_id: str):
    require_role(user, "teacher", "admin")
    with database(request.app.state.settings) as db:
        run = get_run(db, user, run_id)
        items = [dict(row) for row in db.execute(
            """SELECT rv.id,rv.version,rv.kind,rv.parent_id,rv.created_at,
                      r.decision,r.reason,r.reviewer_id,er.report_id IS NOT NULL AS effective,
                      json_extract(rv.body,'$.system_quality_review') AS quality_review
               FROM report_versions rv LEFT JOIN reviews r ON r.report_id=rv.id
               LEFT JOIN effective_reports er ON er.report_id=rv.id WHERE rv.run_id=? ORDER BY rv.version DESC""", (run["id"],)
        )]
        for item in items:
            item["changes"] = [{"target": row["target"], "before": json.loads(row["before"]), "after": json.loads(row["after"])}
                               for row in db.execute("""SELECT rc.* FROM review_changes rc JOIN reviews r ON r.id=rc.review_id
                                                       WHERE r.report_id=? ORDER BY rc.target""", (item["id"],))]
            quality = json.loads(item.pop("quality_review") or "null")
            if quality and item["kind"] == "system":
                item["reason"] = quality["reason"]
                item["changes"] = quality["changes"]
        return {"items": items, "parent_run_id": run["parent_run_id"]}
