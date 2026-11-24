import json
import mimetypes
from datetime import datetime
from typing import Literal

from fastapi import Request
from fastapi.responses import Response
from pydantic import BaseModel, Field, model_validator

from .adapters import Identifier, RunFinished, TestFinished, Text
from .api import router, session_detail
from .core import ROOT, Problem, database, digest, dump, get_session, now, require_role, row_or_404, uid
from .evaluation import DemoProvider, active_config
from .http import User

TASK_PACKAGES = ROOT / "backend" / "task_packages"


class Start(BaseModel):
    task_id: Identifier
    client_event_id: Identifier


class Action(BaseModel):
    client_event_id: Identifier


class ClientEvent(Action):
    type: Literal["code_snapshot", "editor_focus"]
    client_time: datetime | None = None
    content: Text | None = None
    expected_artifact_version: int | None = Field(default=None, ge=0, strict=True)

    @model_validator(mode="after")
    def requires_code_version(self):
        if self.type == "code_snapshot" and (self.content is None or self.expected_artifact_version is None):
            raise ValueError("保存代码版本时需要完整代码和预期版本")
        return self


class Chat(Action):
    text: str = Field(min_length=1, max_length=16_000)


class Snapshot(Action):
    content: Text
    expected_artifact_version: int = Field(ge=0, strict=True)
    artifact_checksum: str = Field(pattern=r"^[a-f0-9]{64}$")


class Submit(Snapshot):
    statement: Text = ""
    cleaned_data: Text = ""
    trend: Text = ""


def event(db, session_id, event_key, kind, payload):
    number = db.execute("SELECT COALESCE(MAX(sequence_no),0)+1 FROM events WHERE session_id=?",
                        (session_id,)).fetchone()[0]
    event_id = uid()
    db.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?,?)",
               (event_id, session_id, number, event_key, kind, now(), "server", dump(payload)))
    return event_id


def prior_event(db, session_id, key, digest_value):
    previous = db.execute("SELECT * FROM events WHERE session_id=? AND event_key=?", (session_id, key)).fetchone()
    if previous:
        payload = json.loads(previous["payload"])
        if payload.get("request_hash") != digest_value:
            raise Problem(409, "IDEMPOTENCY_CONFLICT", "同一操作编号已用于不同内容。")
        return payload
    return None


def owned_live(db, user, sid):
    session = get_session(db, user, sid, "manage")
    if session["source_type"] != "live" or session["owner_user_id"] != user["id"]:
        raise Problem(403, "LIVE_OWNER", "仅作答会话本人可以继续记录。")
    return session


def ensure_running(session):
    if session["status"] != "in_progress":
        raise Problem(409, "SESSION_FROZEN", "已提交版本被冻结，请创建新尝试。")


def active_code(db, sid):
    return db.execute("SELECT * FROM artifacts WHERE session_id=? AND kind='code' ORDER BY version DESC LIMIT 1",
                      (sid,)).fetchone()


def save_snapshot(db, sid, content, expected, key, request_hash, client_time=None):
    current = active_code(db, sid)
    version = current["version"] if current else 0
    if version != expected:
        raise Problem(409, "ARTIFACT_VERSION", "代码已在其他操作中保存，请查看最新版本后确认。",
                      {"current_version": version})
    checksum = digest(content)
    if current and current["checksum"] == checksum:
        artifact_id = current["id"]
    else:
        artifact_id = uid()
        version += 1
        db.execute("INSERT INTO artifacts VALUES (?,?,?,?,?,?,?)",
                   (artifact_id, sid, "code", version, checksum, content, "text/x-python"))
    payload = {"artifact_id": artifact_id, "language": "python", "content": content, "version": version,
               "artifact_checksum": checksum, "request_hash": request_hash}
    if client_time:
        payload["client_time"] = client_time
    event(db, sid, key, "code_snapshot", payload)
    return payload


def snapshot_response(payload):
    return {"version": payload["version"], "checksum": payload["artifact_checksum"], "id": payload["artifact_id"]}


def public_task(row):
    metadata = json.loads(row["metadata"])
    tests = json.loads(row["tests"])
    rubric = json.loads(row["rubric"])
    campus = metadata.get("bundle") == "campus-sales"
    if metadata.get("bundle") == "campus-sales":
        package = TASK_PACKAGES / "campus-sales"
        tests = json.loads((package / "public_tests.json").read_text(encoding="utf-8"))
        rubric = json.loads((package / "rubric.json").read_text(encoding="utf-8"))
    return {key: row[key] for key in ("id", "version", "title", "instructions", "status", "course_id")} | {
        "minutes": metadata.get("minutes"), "deliverables": metadata.get("deliverables", []),
        "has_material": campus or bool(metadata.get("materials")), "public_tests": tests, "rubric": rubric,
        "task_type": metadata.get("task_type", "数据清洗" if campus else "课程作业"),
        "difficulty": metadata.get("difficulty", "初级" if campus else None), "ai_allowed": metadata.get("ai_allowed", True),
        "goal": metadata.get("goal", "清洗订单，核对退款和月净销售额。" if campus else row["instructions"]),
        "materials": ["campus-sales.csv"] if campus else metadata.get("materials", []),
        "attribution": metadata.get("attribution"), "static_demo": campus,
        "result_label": metadata.get("result_label", "清洗结果 CSV"), "check_label": metadata.get("check_label", "趋势图数据或 SVG 源码"),
    }


@router.get("/task-definitions")
def list_tasks(request: Request, user: User):
    with database(request.app.state.settings) as db:
        rows = db.execute("""SELECT * FROM task_definitions WHERE status='published' OR
                             (status='draft' AND (? OR owner_user_id=?)) ORDER BY rowid""",
                          (user["role"] == "admin", user["id"]))
        return {"items": [public_task(row) for row in rows]}


@router.get("/task-definitions/{task_id}/material")
def task_material(request: Request, user: User, task_id: str, file: str | None = None):
    with database(request.app.state.settings) as db:
        task = row_or_404(db.execute("SELECT * FROM task_definitions WHERE id=? AND status='published'", (task_id,)).fetchone())
        metadata = json.loads(task["metadata"])
        if metadata.get("bundle") == "campus-sales" and file in (None, "campus-sales.csv"):
            return Response((TASK_PACKAGES / "campus-sales" / "input.csv").read_bytes(), media_type="text/csv",
                            headers={"Content-Disposition": 'attachment; filename="campus-sales.csv"'})
        if metadata.get("bundle") != task_id or file not in metadata.get("materials", []):
            raise Problem(404, "MATERIAL_MISSING", "该任务没有上传材料。")
        material = TASK_PACKAGES / task_id / file
        if not material.is_file():
            raise Problem(404, "MATERIAL_MISSING", "任务包中的文件不可用。")
        return Response(material.read_bytes(), media_type=mimetypes.guess_type(file)[0] or "application/octet-stream",
                        headers={"Content-Disposition": f'attachment; filename="{file}"'})


@router.post("/live-sessions", status_code=201)
def start(request: Request, user: User, body: Start):
    settings = request.app.state.settings
    key = "open/" + body.client_event_id
    with database(settings, write=True) as db:
        task = row_or_404(db.execute("SELECT * FROM task_definitions WHERE id=? AND status='published'", (body.task_id,)).fetchone())
        if user["role"] != "student" and not settings.demo:
            require_role(user, "student")
        old = db.execute("""SELECT s.* FROM sessions s JOIN events e ON e.session_id=s.id
                            WHERE s.owner_user_id=? AND e.event_key=? AND e.type='task_opened'""",
                         (user["id"], key)).fetchone()
        if old:
            if old["task_id"] != task["id"]:
                raise Problem(409, "IDEMPOTENCY_CONFLICT", "同一开始编号已用于另一任务。")
            return session_detail(db, old)
        sid = uid()
        db.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   (sid, "live", None, None, user["id"], task["course_id"], task["id"], "in_progress", None, "{}", now()))
        event(db, sid, key, "task_opened", {"task_version": task["version"]})
        return session_detail(db, db.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone())


@router.get("/live-sessions/{sid}")
def workspace(request: Request, user: User, sid: str):
    with database(request.app.state.settings) as db:
        session = get_session(db, user, sid)
        if session["source_type"] != "live":
            raise Problem(404, "NOT_LIVE", "当前记录不是真人任务。")
        task = db.execute("SELECT * FROM task_definitions WHERE id=?", (session["task_id"],)).fetchone()
        current = active_code(db, sid)
        messages = []
        rows = db.execute("""SELECT type,payload FROM events WHERE session_id=? AND type IN
                           ('chat_user_message','chat_ai_message') ORDER BY sequence_no DESC LIMIT 100""", (sid,))
        for row in reversed(rows.fetchall()):
            payload = json.loads(row["payload"])
            messages.append({"type": row["type"], "text": payload["text"], "simulated": payload.get("simulated", False)})
        return {"id": sid, "task": public_task(task), "status": session["status"], "messages": messages,
                "can_edit": session["owner_user_id"] == user["id"] and session["status"] == "in_progress",
                "artifact_version": current["version"] if current else 0, "content": current["content"] if current else ""}


@router.post("/live-sessions/{sid}/events")
def append_event(request: Request, user: User, sid: str, body: ClientEvent):
    key, fingerprint = "event/" + body.client_event_id, digest(dump(body.model_dump(mode="json")))
    with database(request.app.state.settings, write=True) as db:
        session = owned_live(db, user, sid)
        prior = prior_event(db, sid, key, fingerprint)
        if prior:
            return snapshot_response(prior) if body.type == "code_snapshot" else {"saved": True}
        ensure_running(session)
        stamp = body.client_time.isoformat() if body.client_time else None
        if body.type == "editor_focus":
            event(db, sid, key, body.type, {"client_time": stamp, "request_hash": fingerprint})
            return {"saved": True}
        snapshot = save_snapshot(db, sid, body.content, body.expected_artifact_version, key, fingerprint, stamp)
        return snapshot_response(snapshot)


@router.post("/live-sessions/{sid}/chat")
def chat(request: Request, user: User, sid: str, body: Chat):
    key, fingerprint = "chat/" + body.client_event_id, digest(dump(body.model_dump()))
    if not body.text.strip():
        raise Problem(422, "EMPTY_MESSAGE", "请先输入消息。")
    with database(request.app.state.settings, write=True) as db:
        session = owned_live(db, user, sid)
        previous = prior_event(db, sid, key, fingerprint)
        if previous:
            reply = db.execute("SELECT payload FROM events WHERE session_id=? AND event_key=?", (sid, key + "/reply")).fetchone()
            return json.loads(reply["payload"])
        ensure_running(session)
        rows = db.execute("""SELECT type,payload FROM events WHERE session_id=? AND type IN
                           ('chat_user_message','chat_ai_message') ORDER BY sequence_no DESC LIMIT 24""", (sid,))
        history = [{"role": "assistant" if row["type"] == "chat_ai_message" else "user",
                    "text": json.loads(row["payload"])["text"]} for row in reversed(rows.fetchall())]
        history.append({"role": "user", "text": body.text.strip()})
        task = db.execute("SELECT title,instructions,metadata FROM task_definitions WHERE id=?", (session["task_id"],)).fetchone()
        metadata = json.loads(task["metadata"])
        if metadata.get("ai_allowed") is False:
            raise Problem(409, "TASK_AI_DISABLED", "此任务不允许使用 AI 对话。")
        response = metadata.get("ai_hint") or DemoProvider().chat(history, dict(task))
        event(db, sid, key, "chat_user_message", {"text": body.text.strip(), "request_hash": fingerprint})
        payload = {"text": response, "provider": "demo", "simulated": True}
        event(db, sid, key + "/reply", "chat_ai_message", payload)
        return payload


def check_checksum(body):
    if digest(body.content) != body.artifact_checksum:
        raise Problem(409, "CHECKSUM_MISMATCH", "代码内容与提交校验值不同，请重新确认。")


@router.post("/live-sessions/{sid}/runs")
def run_static(request: Request, user: User, sid: str, body: Snapshot):
    check_checksum(body)
    key, fingerprint = "run/" + body.client_event_id, digest(dump(body.model_dump()))
    with database(request.app.state.settings, write=True) as db:
        session = owned_live(db, user, sid)
        previous = prior_event(db, sid, key, fingerprint)
        if previous:
            return previous["result"]
        ensure_running(session)
        task = db.execute("SELECT metadata FROM task_definitions WHERE id=?", (session["task_id"],)).fetchone()
        if json.loads(task["metadata"]).get("bundle") != "campus-sales":
            raise Problem(409, "NO_STATIC_DEMO", "该任务可提交实际输出和检查记录。工作台未配置代码执行。")
        artifact = save_snapshot(db, sid, body.content, body.expected_artifact_version, key + "/snapshot", fingerprint)
        base = {"artifact_id": artifact["artifact_id"], "artifact_version": artifact["version"],
                "artifact_checksum": artifact["artifact_checksum"], "execution_mode": "fixture_simulation",
                "synthetic": True, "verified": False}
        run = RunFinished(**base, exit_code=0, stdout="内置样例输出：1 月净额 20.00，2 月净额 40.00。", stderr="").model_dump()
        tests = TestFinished(**base, passed=2, failed=1, cases=[
            {"id": "unique_orders", "passed": True}, {"id": "refund_kept", "passed": True},
            {"id": "monthly_net", "passed": False, "message": "模拟失败：缺少边界月份核对。"},
        ]).model_dump()
        result = {"run": run, "test": tests, "artifact_version": artifact["version"],
                  "message": "静态演示，仅展示内置案例。您提交的代码没有被执行。"}
        event(db, sid, key, "run_started", {**base, "request_hash": fingerprint, "result": result})
        event(db, sid, key + "/finished", "run_finished", run)
        event(db, sid, key + "/tested", "test_finished", tests)
        return result


@router.post("/live-sessions/{sid}/submit", status_code=202)
def submit(request: Request, user: User, sid: str, body: Submit):
    check_checksum(body)
    fingerprint = digest(dump(body.model_dump()))
    key = "submit/" + body.client_event_id
    with database(request.app.state.settings, write=True) as db:
        session = owned_live(db, user, sid)
        existing = db.execute("SELECT * FROM submissions WHERE session_id=?", (sid,)).fetchone()
        if existing and existing["client_event_id"] == body.client_event_id:
            if existing["request_hash"] != fingerprint:
                raise Problem(409, "IDEMPOTENCY_CONFLICT", "同一提交编号已用于不同成果。")
            return {"submission_id": existing["id"], "session_id": sid, "status": "submitted"}
        ensure_running(session)
        code = save_snapshot(db, sid, body.content, body.expected_artifact_version, key + "/snapshot", fingerprint)
        refs = [code["artifact_id"]]
        for kind, content, mime in (
            ("cleaned_data", body.cleaned_data, "text/csv"), ("trend", body.trend, "text/plain"),
            ("statement", body.statement, "text/plain"),
        ):
            if content.strip():
                artifact_id = uid()
                checksum = digest(content)
                refs.append(artifact_id)
                db.execute("INSERT INTO artifacts VALUES (?,?,?,?,?,?,?)", (artifact_id, sid, kind, 1, checksum, content, mime))
                event(db, sid, key + "/" + kind, "artifact_saved", {
                    "artifact_id": artifact_id, "kind": kind, "checksum": checksum, "content": content
                })
        submission_id = uid()
        event(db, sid, key, "submission_created", {"artifact_refs": refs, "artifact_checksum": code["artifact_checksum"],
                                                 "artifact_version": code["version"], "statement": body.statement})
        db.execute("INSERT INTO submissions VALUES (?,?,?,?,?,?)", (
            submission_id, sid, code["artifact_id"], body.client_event_id, fingerprint, now()
        ))
        db.execute("UPDATE sessions SET status='submitted' WHERE id=?", (sid,))
        db.execute("INSERT INTO outbox VALUES (?,?,?,NULL)", (submission_id, sid, active_config(db)))
        return {"submission_id": submission_id, "session_id": sid, "status": "submitted"}


@router.delete("/live-sessions/{sid}")
def delete_attempt(request: Request, user: User, sid: str):
    with database(request.app.state.settings, write=True) as db:
        owned_live(db, user, sid)
        if db.execute("SELECT 1 FROM evaluation_runs WHERE session_id=? AND purpose='research'", (sid,)).fetchone():
            raise Problem(403, "RESEARCH_RETAINED", "该会话已进入正式研究，不可由学生直接删除。")
        counts = {name: db.execute(f"SELECT COUNT(*) FROM {name} WHERE session_id=?", (sid,)).fetchone()[0]
                  for name in ("events", "artifacts", "report_versions")}
        db.execute("DELETE FROM sessions WHERE id=?", (sid,))
        db.execute("INSERT INTO deletion_audit VALUES (?,?,?,?,?)", (uid(), user["id"], "live_session", dump(counts), now()))
        return {"deleted": counts}
