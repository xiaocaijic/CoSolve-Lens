import json
from collections import Counter
from typing import Literal
from uuid import UUID

from fastapi import Request
from pydantic import BaseModel, Field

from .api import router
from .core import Problem, database, digest, dump, get_session, get_source, now, require_role
from .evaluation import event_payloads
from .five_provider import CHAT_TYPES, PILOT_SCOPE, dialogue_chunks, outbound_messages
from .http import User
from .jobs import public_job, schedule_evaluation
from .outcomes import source_grade
from .providers import deployment_key, provider_config
from .showcase import entry_settings
from .studychat_policy import is_snapshot
from .studychat_quality import provenance, quality_check, session_quality


class AnalysisScope(BaseModel):
    source_id: str
    session_ids: list[str] = Field(max_length=5)
    model: Literal["rules", "deepseek-flash"]
    max_count: int = Field(ge=1, le=5)
    term: str = ""
    assignment: str = ""


class AnalysisStart(AnalysisScope):
    checked_scope: str
    request_key: str = Field(pattern=r"^[a-f0-9-]{36}$")
    confirm_model_scope: bool = False


def task_scope(title, studychat):
    if studychat and " · " in title:
        return tuple(title.split(" · ", 1))
    return "", title


def scoped_authorizations(db, source_id):
    rows = db.execute("""SELECT m.* FROM studychat_sample_members m JOIN jobs j ON j.id=m.job_id
        LEFT JOIN analysis_batches b ON b.job_id=j.id WHERE j.source_id=? AND b.job_id IS NULL
        AND m.scope_version=? AND m.model='deepseek-flash' ORDER BY j.rowid""", (source_id, PILOT_SCOPE))
    return {row["session_id"]: row for row in rows}


@router.get("/datasets/{source_id}/analysis-options")
def options(request: Request, user: User, source_id: str):
    require_role(user, "teacher", "admin")
    with database(request.app.state.settings) as db:
        source = get_source(db, user, source_id, "evaluate")
        studychat = is_snapshot(source)
        authorized = scoped_authorizations(db, source_id) if studychat and user["role"] == "admin" else {}
        available = False
        reason = "该资料范围当前只允许本地规则分析。"
        if studychat and user["role"] == "teacher":
            reason = "教师入口可运行本地规则。DeepSeek 需从管理员入口确认逐条授权的会话。"
        if authorized:
            profile = provider_config(db)
            available = (profile["provider"] == "deepseek" and profile["model"] == "deepseek-flash"
                         and profile["enabled"] and profile["retention_confirmed"]
                         and bool(deployment_key("deepseek", request.app.state.settings)))
            reason = ("可使用 DeepSeek 分析已有逐条授权的会话。" if available
                      else "已有逐条授权的会话。启用 DeepSeek Flash 配置后可选用模型。")
        rows = db.execute("""SELECT s.id,t.title,substr(p.external_key_hash,1,10) as participant
            FROM sessions s JOIN task_definitions t ON t.id=s.task_id LEFT JOIN participants p ON p.id=s.participant_id
            WHERE s.source_id=? AND s.status='submitted' ORDER BY t.title,s.id""", (source_id,))
        items = []
        for row in rows:
            term, assignment = task_scope(row["title"], studychat)
            items.append(dict(row) | {"term": term, "assignment": assignment, "deepseek_authorized": row["id"] in authorized})
        return {"items": items, "studychat": studychat, "deepseek_available": available, "deepseek_reason": reason}


@router.get("/datasets/{source_id}/studychat-chat/{chat_id}")
def find_original_chat(request: Request, user: User, source_id: str, chat_id: UUID):
    require_role(user, "teacher", "admin")
    with database(request.app.state.settings) as db:
        if not is_snapshot(get_source(db, user, source_id)):
            raise Problem(404, "NOT_STUDYCHAT", "该来源没有 StudyChat 原始会话。")
        raw = next((entry for entry in provenance().values() if entry and entry[0] == str(chat_id) and len(entry) >= 6), None)
        task_filter = f"{raw[4].upper()} · {raw[5].upper()}" if raw and raw[4] and raw[5] else None
        rows = db.execute("""SELECT s.id,t.instructions FROM sessions s JOIN task_definitions t ON t.id=s.task_id
            WHERE s.source_id=? AND (? IS NULL OR t.title=?)""", (source_id, task_filter, task_filter)).fetchall()
        for row in rows:
            quality = quality_check(row, event_payloads(db, row["id"], (1 << 63) - 1),
                                    grade_matched=False, submission_matched=False)
            if str(chat_id) in quality["source_chat_ids"]:
                return {"session_id": row["id"]}
    return {"session_id": None}


def inspect(db, settings, user, scope):
    source = get_source(db, user, scope.source_id, "evaluate")
    ids = sorted(set(scope.session_ids))
    if not 1 <= len(ids) == len(scope.session_ids) <= scope.max_count:
        raise Problem(422, "ANALYSIS_SCOPE", "请选择不重复的 1–5 条会话，数量须在本次上限内。")
    studychat = is_snapshot(source)
    remote = scope.model == "deepseek-flash"
    approved = scoped_authorizations(db, source["id"]) if studychat and remote and user["role"] == "admin" else {}
    blocked_reason = None
    if remote:
        profile = provider_config(db)
        if studychat and not entry_settings(db).live_enabled:
            blocked_reason = "管理员已关闭 StudyChat 现场分析入口。"
        elif not studychat or user["role"] != "admin":
            blocked_reason = "请从管理员入口启动已授权的 StudyChat 模型评价。"
        elif not deployment_key("deepseek", settings):
            blocked_reason = "请先在管理员系统设置配置 DeepSeek 密钥。"
        elif (profile["provider"] != "deepseek" or profile["model"] != "deepseek-flash"
                or not profile["enabled"] or not profile["retention_confirmed"]):
            blocked_reason = "请在管理员系统设置启用 DeepSeek Flash 并确认用途和保留策略。"
    sessions, summaries, stamps = [], [], []
    missing, coverage = Counter(), Counter()
    expected_calls = maximum_calls = 0
    for sid in ids:
        session = get_session(db, user, sid, "evaluate")
        task = db.execute("SELECT title,instructions FROM task_definitions WHERE id=?", (session["task_id"],)).fetchone()
        term, assignment = task_scope(task["title"], studychat)
        if (session["source_id"] != source["id"] or session["status"] != "submitted" or
                (scope.term and term != scope.term) or (scope.assignment and assignment != scope.assignment)):
            raise Problem(422, "ANALYSIS_SCOPE", "所选会话超出本次数据源、学期或作业范围。")
        events = event_payloads(db, sid, (1 << 63) - 1)
        messages = [event for event in events if event["type"] in CHAT_TYPES]
        turns = sum(event["type"] == CHAT_TYPES[0] for event in messages)
        grade = source_grade(json.loads(session["metadata"])) is not None
        stored = {row[0] for row in db.execute("SELECT id FROM artifacts WHERE session_id=?", (sid,))}
        attached = {key for event in events if event["type"] == "submission_created" for key in event["payload"].get("artifact_refs", [])}
        files = len(stored & attached)
        runs = any(event["type"] in ("run_finished", "test_finished") and event["payload"].get("verified")
                   and not event["payload"].get("synthetic") for event in events)
        checks = {"无任务说明": bool(task["instructions"]), "无对话": bool(messages), "无来源成绩": grade,
                  "无提交物": bool(files), "无可核验运行": runs}
        missing.update(key for key, present in checks.items() if not present)
        if turns > sum(event["type"] == CHAT_TYPES[1] for event in messages):
            missing["有轮次缺少 AI 回复"] += 1
        coverage[json.loads(session["metadata"]).get("coverage") or "none"] += 1
        quality = session_quality(db, session, task, events) if studychat else None
        if not studychat or quality["eligibility_status"] == "eligible":
            sessions.append(session)
        if remote and quality and quality["eligibility_status"] == "eligible":
            member = approved.get(sid)
            if not member:
                blocked_reason = blocked_reason or "所选合格会话缺少逐条外发授权。"
            elif len(messages) != member["verified_dialogue_messages"]:
                blocked_reason = "所选会话已变化，需重新核查完整对话和外发范围。"
            allowed = set(quality["scoped_event_ids"])
            clean, _ = outbound_messages([event for event in events if event["id"] in allowed], studychat=True)
            # The provider uses short aliases. Mirror the exact segmentation calculation.
            aliases = {event["id"]: f"E{event['sequence_no']:04d}" for event in events}
            chunks = len(dialogue_chunks([{**item, "event_id": aliases[item["event_id"]]} for item in clean]))
            expected_calls += chunks + (chunks + 1 if chunks > 1 else 1)
            maximum_calls += 2 * chunks + (2 * chunks + 3 if chunks > 1 else 3)
        stamps.append((sid, events[-1]["sequence_no"] if events else 0))
        summaries.append({"session_id": sid, "title": task["title"], "turns": turns, "messages": len(messages),
                          "grade_matched": grade, "submission_matched": bool(files), "quality": quality})
    if maximum_calls > 100:
        raise Problem(422, "ANALYSIS_CALL_LIMIT", "完整对话分段后的模型调用预计超过上限，请缩小范围。")
    result = {"checked_scope": digest(dump([user["id"], scope.source_id, scope.model, scope.term, scope.assignment, scope.max_count, stamps])),
              "count": len(summaries), "turns": sum(row["turns"] for row in summaries), "messages": sum(row["messages"] for row in summaries),
              "grade_matches": sum(row["grade_matched"] for row in summaries), "submission_matches": sum(row["submission_matched"] for row in summaries),
              "valid_grade_links": sum(row["quality"]["grade_link_valid"] for row in summaries if row["quality"]),
              "missing": dict(missing), "coverage": dict(coverage), "sessions": summaries,
              "studychat": studychat, "eligible_count": len(sessions), "blocked_reason": blocked_reason,
              "complete_count": sum(not row["quality"]["exclusion_reasons"] or "incomplete" not in row["quality"]["exclusion_reasons"]
                                    for row in summaries if row["quality"]),
              "relevant_count": sum(row["quality"]["task_relevance"] == "relevant" for row in summaries if row["quality"]),
              "rich_count": sum(bool(row["quality"]["richness_signals"]) for row in summaries if row["quality"]),
              "expected_calls": expected_calls, "maximum_calls": maximum_calls}
    return sessions, approved, result, source


@router.post("/analysis/inspect")
def check_scope(request: Request, user: User, body: AnalysisScope):
    require_role(user, "teacher", "admin")
    settings = request.app.state.settings
    with database(settings) as db:
        return inspect(db, settings, user, body)[2]


@router.post("/analysis/start", status_code=202)
def start_analysis(request: Request, user: User, body: AnalysisStart):
    require_role(user, "teacher", "admin")
    settings = request.app.state.settings
    with database(settings, write=True) as db:
        sessions, approved, checked, source = inspect(db, settings, user, body)
        if checked["checked_scope"] != body.checked_scope:
            raise Problem(409, "ANALYSIS_CHECK_REQUIRED", "范围或事件已变化，请重新检查后再分析。")
        remote = body.model == "deepseek-flash"
        if checked["studychat"] and not entry_settings(db).live_enabled:
            raise Problem(409, "STUDYCHAT_LIVE_DISABLED", "管理员已关闭 StudyChat 现场分析入口。")
        if checked["studychat"] and not remote:
            raise Problem(409, "STUDYCHAT_CHECK_ONLY", "本地规则只用于 StudyChat 数据质量检查。")
        if not sessions:
            raise Problem(422, "STUDYCHAT_INELIGIBLE", "当前范围没有通过资格检查的会话。")
        if remote and checked["blocked_reason"]:
            raise Problem(409, "ANALYSIS_EXTERNAL_SCOPE", checked["blocked_reason"])
        if remote and not body.confirm_model_scope:
            raise Problem(422, "ANALYSIS_EXTERNAL_CONFIRM", "请确认本次已授权会话的模型调用范围。")
        method = "hybrid" if remote else "rules"
        purpose = "demo" if source["demo"] else "research"
        job = schedule_evaluation(db, user["id"], sessions, method, purpose, source_id=source["id"],
                                  request_key=body.request_key, sample_id=body.request_key if remote else None)
        created = db.execute("INSERT OR IGNORE INTO analysis_batches(job_id,model,provider_call_limit) VALUES (?,?,?)",
                             (job["id"], body.model, checked["maximum_calls"])).rowcount
        if created:
            db.execute("""UPDATE evaluation_runs AS fresh SET parent_run_id=(SELECT r.run_id FROM effective_reports er
                JOIN report_versions r ON r.id=er.report_id WHERE er.session_id=fresh.session_id AND er.method=fresh.method
                AND er.purpose=fresh.purpose) WHERE fresh.job_id=?""", (job["id"],))
            if remote:
                for session in sessions:
                    member = approved[session["id"]]
                    db.execute("INSERT INTO studychat_sample_members VALUES (?,?,?,?,?,?,?,?)",
                               (job["id"], session["id"], "deepseek-flash", PILOT_SCOPE,
                                "现场分析按既有逐会话授权及管理员本次确认执行。", member["submission_summary"],
                                member["verified_dialogue_messages"], now()))
                    db.execute("""INSERT INTO studychat_sample_evidence_review
                        SELECT ?,session_id,event_id,finding FROM studychat_sample_evidence_review WHERE job_id=? AND session_id=?""",
                               (job["id"], member["job_id"], session["id"]))
                    quality = next(row["quality"] for row in checked["sessions"] if row["session_id"] == session["id"])
                    run_id = db.execute("SELECT id FROM evaluation_runs WHERE job_id=? AND session_id=?",
                                        (job["id"], session["id"])).fetchone()[0]
                    db.execute("INSERT INTO studychat_run_quality(run_id,body) VALUES (?,?)", (run_id, dump(quality)))
        return public_job(db, job)
