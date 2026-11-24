import hmac
import json
import os
import secrets
import time
from typing import Annotated

from fastapi import Depends, Query, Request

from .core import Problem, database, digest, get_session, get_source, row_or_404

COOKIE = "ai_collab_session"
TTL = 60 * 60 * 8
Page = Annotated[int, Query(ge=1, le=1_000_000)]
PageSize = Annotated[int, Query(ge=1, le=100)]


def current_user(request: Request):
    token = request.cookies.get(COOKIE, "")
    with database(request.app.state.settings) as db:
        user = db.execute(
            """SELECT u.id,u.role,u.display_name,a.csrf FROM auth_sessions a JOIN users u ON u.id=a.user_id
               WHERE a.token_hash=? AND a.expires_at>?""", (digest(token), time.time())
        ).fetchone()
    if not user:
        raise Problem(401, "LOGIN_REQUIRED", "请先登录。")
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        csrf = request.headers.get("x-csrf-token", "")
        if not hmac.compare_digest(csrf.encode(), user["csrf"].encode()):
            raise Problem(403, "CSRF", "操作校验已失效，请刷新后重试。")
    return dict(user)


User = Annotated[dict, Depends(current_user)]


def validate_demo_origin(request):
    origin = request.headers.get("origin")
    allowed = {os.getenv("AI_COLLAB_WEB_ORIGIN", "http://127.0.0.1:5173"), "http://localhost:5173"}
    if origin and origin not in allowed:
        raise Problem(403, "ORIGIN_FORBIDDEN", "请从本地应用页面登录。")


def create_login(db, user):
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    db.execute("DELETE FROM auth_sessions WHERE expires_at<=?", (time.time(),))
    db.execute("INSERT INTO auth_sessions VALUES (?,?,?,?)", (digest(token), user["id"], csrf, time.time() + TTL))
    return token, {"id": user["id"], "role": user["role"], "display_name": user["display_name"], "csrf": csrf}


def get_job(db, user, job_id, permission="view"):
    job = row_or_404(db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
    if job["source_id"]:
        get_source(db, user, job["source_id"], permission)
    elif job["session_id"]:
        get_session(db, user, job["session_id"], permission)
    elif job["owner_user_id"] != user["id"] and user["role"] != "admin":
        raise Problem(403, "JOB_FORBIDDEN", "您没有该后台任务的操作权限。")
    return job


def get_run(db, user, run_id, permission="view"):
    run = row_or_404(db.execute("SELECT * FROM evaluation_runs WHERE id=?", (run_id,)).fetchone())
    get_session(db, user, run["session_id"], permission)
    return run


def get_report(db, user, report_id, permission="view"):
    from .evaluation import five_point_score
    from .guidance import report_guidance
    from .outcomes import report_outcomes
    from .rubrics import is_five_dimension
    from .studychat_policy import is_snapshot

    row = row_or_404(db.execute("SELECT * FROM report_versions WHERE id=?", (report_id,)).fetchone())
    session = get_session(db, user, row["session_id"], permission)
    rejected = db.execute("SELECT reason FROM studychat_quality_rejections WHERE report_id=?", (row["id"],)).fetchone()
    if rejected:
        raise Problem(410, "QUALITY_REJECTED", "该报告已按数据质量规则排除，可查看原始会话和排除原因。")
    effective = db.execute("SELECT * FROM effective_reports WHERE report_id=?", (report_id,)).fetchone()
    if user["role"] == "student" and not (effective and effective["released_to_student"]):
        raise Problem(403, "REPORT_NOT_RELEASED", "该版本尚未发布给学生。")
    can_review = False
    if user["role"] != "student":
        try:
            get_session(db, user, row["session_id"], "review")
            can_review = True
        except Problem as exc:
            if exc.status != 403:
                raise
    source = db.execute("SELECT adapter,source_identity FROM data_sources WHERE id=?", (session["source_id"],)).fetchone()
    report = report_outcomes(db, json.loads(row["body"]), json.loads(session["metadata"]), is_snapshot(source))
    previous_run = db.execute("SELECT parent_run_id,event_cutoff FROM evaluation_runs WHERE id=?", (row["run_id"],)).fetchone()
    if previous_run and is_snapshot(source):
        report = report_guidance(db, report, row["session_id"], previous_run["event_cutoff"])
        report["evidence_turns"] = {event["id"]: {"turn": event["turn"], "sequence": event["sequence_no"]}
                                    for event in db.execute("""SELECT id, sequence_no, SUM(type='chat_user_message') OVER (
            ORDER BY sequence_no) AS turn FROM events WHERE session_id=? AND sequence_no<=?
            AND type IN ('chat_user_message','chat_ai_message') ORDER BY sequence_no""",
                                        (row["session_id"], previous_run["event_cutoff"]))}
    return {**report, "process_score_five": report.get("process_score_five", five_point_score(report["process_score"])),
            "legacy_rubric": not is_five_dimension(report["rubric_version"]),
            "parent_run_id": previous_run[0] if previous_run else None,
            "effective": bool(effective), "can_review": can_review,
            "released_to_student": bool(effective and effective["released_to_student"])}


def page_result(items, page, size):
    start = (page - 1) * size
    return {"items": items[start:start + size], "total": len(items), "page": page, "page_size": size}
