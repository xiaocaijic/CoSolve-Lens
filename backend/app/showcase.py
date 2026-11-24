"""Read-only classroom replays and the administrator's entry settings."""
import json
from functools import lru_cache
from typing import Literal

from fastapi import Request
from pydantic import BaseModel, model_validator

from .api import router
from .builtin_studychat import IDENTITY
from .core import ROOT, Problem, database, pseudonym, require_role, source_access
from .five_provider import CHAT_TYPES, clean_text
from .http import User


class EntrySettings(BaseModel):
    replay_enabled: bool
    live_enabled: bool
    default_mode: Literal["replay", "live"]

    @model_validator(mode="after")
    def enabled_default(self):
        if not (self.replay_enabled if self.default_mode == "replay" else self.live_enabled):
            raise ValueError("默认入口必须处于开放状态。")
        return self


def entry_settings(db):
    row = db.execute("SELECT replay_enabled,live_enabled,default_mode FROM studychat_showcase_settings WHERE id=1").fetchone()
    return EntrySettings.model_validate(dict(row))


@router.get("/studychat/entry-settings")
def read_entries(request: Request, user: User):
    require_role(user, "teacher", "admin")
    with database(request.app.state.settings) as db:
        return entry_settings(db)


@router.put("/settings/studychat-entries")
def save_entries(request: Request, user: User, body: EntrySettings):
    require_role(user, "admin")
    with database(request.app.state.settings, write=True) as db:
        db.execute("UPDATE studychat_showcase_settings SET replay_enabled=?,live_enabled=?,default_mode=? WHERE id=1",
                   (body.replay_enabled, body.live_enabled, body.default_mode))
    return body


@lru_cache(maxsize=1)
def catalog():
    return json.loads((ROOT / "data" / "studychat-showcase-v1.json").read_text(encoding="utf-8"))


def matched_sessions(db, user, settings):
    sources = db.execute("SELECT * FROM data_sources WHERE adapter='studychat' AND source_identity=? ORDER BY rowid DESC", (IDENTITY,))
    key = settings.key()
    cases = {pseudonym(key, IDENTITY + "/session", case["archive_key"]): case for case in catalog()}
    marks = ",".join("?" for _ in cases)
    sessions = {}
    for source in sources:
        if not source_access(db, user, source):
            continue
        for row in db.execute(f"""SELECT s.id,s.external_key_hash,t.title FROM sessions s
            JOIN task_definitions t ON t.id=s.task_id
            WHERE s.source_id=? AND s.external_key_hash IN ({marks})""", (source["id"], *cases)):
            sessions.setdefault(cases[row["external_key_hash"]]["id"], row)
    return sessions


def saved_report(db, case, session, field="report_id"):
    report_id = case.get(field)
    if not session or not report_id:
        return None
    row = db.execute("""SELECT rv.run_id,rv.id,rv.body FROM report_versions rv
        WHERE rv.id=? AND rv.session_id=? AND NOT EXISTS
        (SELECT 1 FROM studychat_quality_rejections q WHERE q.report_id=rv.id)""",
        (report_id, session["id"])).fetchone()
    if not row:
        return None
    report = json.loads(row["body"])
    if report.get("provider") != "deepseek" or not (report.get("provider_call") or {}).get("provider_response_valid"):
        return None
    return {"id": row["id"], "run_id": row["run_id"], "rubric_version": report["rubric_version"],
            "process_score": report["process_score"], "process_score_five": report.get("process_score_five"),
            "source_outcome_percent": report["source_outcome_percent"]}


def case_card(db, case, session):
    return {name: case[name] for name in ("id", "title", "theme", "task", "hook")} | {
        "session_id": session["id"] if session else None, "report": saved_report(db, case, session),
        "historical_report": saved_report(db, case, session, "historical_report_id")}


@router.get("/studychat/showcase")
def list_cases(request: Request, user: User):
    require_role(user, "teacher", "admin")
    settings = request.app.state.settings
    with database(settings) as db:
        sessions = matched_sessions(db, user, settings)
        # The same curated session IDs are used to focus the live selector.
        return {"items": [case_card(db, case, sessions[case["id"]]) for case in catalog() if case["id"] in sessions]}


@router.get("/studychat/showcase/{case_id}")
def read_case(request: Request, user: User, case_id: str):
    require_role(user, "teacher", "admin")
    settings = request.app.state.settings
    with database(settings) as db:
        if not entry_settings(db).replay_enabled:
            raise Problem(403, "SHOWCASE_DISABLED", "课堂回放入口已关闭。")
        case = next((item for item in catalog() if item["id"] == case_id), None)
        if not case:
            raise Problem(404, "SHOWCASE_CASE_NOT_FOUND", "未找到此预备案例。")
        session = matched_sessions(db, user, settings).get(case_id)
        if not session:
            raise Problem(404, "SHOWCASE_SOURCE_UNAVAILABLE", "没有可访问的原始会话。")
        evidence = []
        for reference in case["evidence"]:
            row = db.execute("""SELECT id,type,payload FROM events WHERE session_id=? AND sequence_no=?""",
                             (session["id"], reference["sequence"])).fetchone()
            if not row or row["type"] not in CHAT_TYPES:
                raise Problem(409, "SHOWCASE_EVIDENCE_MISSING", "案例证据与当前快照不一致。")
            cleaned, _ = clean_text(json.loads(row["payload"])["text"])
            text = cleaned.strip()
            evidence.append(reference | {"id": row["id"], "role": "学生" if row["type"] == CHAT_TYPES[0] else "AI 回复",
                                         "excerpt": text[:640], "excerpted": len(text) > 640})
        return case_card(db, case, session) | {name: case[name] for name in ("finding", "exercise")} | {"evidence": evidence}
