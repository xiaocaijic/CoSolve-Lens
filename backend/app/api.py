import json
import re
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, Query, Request, UploadFile
from pydantic import BaseModel, Field, ValidationError

from .adapters import MAX_UPLOAD, prepare_import
from .core import Problem, course_access, database, digest, dump, get_session, get_source, now, require_role, row_or_404, source_access, uid
from .evaluation import five_point_score, short_excerpt
from .http import Page, PageSize, User, get_job, get_report, get_run, page_result
from .jobs import enqueue, public_job, schedule_evaluation
from .outcomes import outcome_fields, report_outcomes
from .rubrics import DimensionName, FIVE_DIMENSION_VERSIONS, is_five_dimension

router = APIRouter(prefix="/api")


class ImportMetadata(BaseModel):
    name: str = Field(default="", max_length=160)
    source_identity: str = Field(default="", max_length=100)
    license_note: str = Field(default="", max_length=1000)
    intended_use: str = Field(default="", max_length=1000)
    retain_until: date | None = None
    mapping: dict[str, str] | None = None
    allow_external_model: bool = False
    mapped_task_id: str | None = Field(default=None, max_length=100)


class ConfirmImport(BaseModel):
    license_confirmed: bool
    only_valid: bool = False


class BatchRequest(BaseModel):
    source_id: str
    session_ids: list[str] = Field(min_length=1, max_length=100)
    method: Literal["rules", "direct_llm", "hybrid", "demo_conflict"] = "hybrid"


class StudyChatLocalRequest(BaseModel):
    semester: Literal["all", "f24", "s25"] = "all"
    assignment: Literal["all", "a1", "a2", "a3", "a4", "a5", "a6", "a7"] = "all"
    name: str = Field(default="", max_length=160)
    license_note: str = Field(min_length=1, max_length=1000)
    intended_use: str = Field(min_length=1, max_length=1000)
    retain_until: date


class StudyChatBuiltinRequest(BaseModel):
    intended_use: str = Field(min_length=1, max_length=1000)
    retain_until: date


class StudyChatConsentRequest(BaseModel):
    enabled: bool
    confirmed: bool
    purpose: str = Field(default="", max_length=1000)


class StudyChatPilotRequest(BaseModel):
    method: Literal["rules", "hybrid"] = "hybrid"


@router.put("/datasets/{source_id}/studychat-consent")
def set_studychat_consent(request: Request, user: User, source_id: str, body: StudyChatConsentRequest):
    from .providers import STUDYCHAT_POLICY_VERSION, provider_config
    from .studychat_policy import is_snapshot

    require_role(user, "admin")
    with database(request.app.state.settings, write=True) as db:
        source = get_source(db, user, source_id, "manage")
        if not is_snapshot(source):
            raise Problem(422, "NOT_STUDYCHAT", "此授权仅适用于 StudyChat 快照。")
        profile = provider_config(db)
        if body.enabled and (not body.confirmed or not body.purpose.strip() or profile["provider"] != "deepseek"
                             or not profile["enabled"] or not profile["retention_confirmed"]):
            raise Problem(422, "STUDYCHAT_CONSENT_REQUIRED", "请先启用 DeepSeek，再确认本数据源的外发范围和用途。")
        db.execute("INSERT INTO studychat_deepseek_consents VALUES (?,?,?,?,?,?,?)",
                   (uid(), source_id, user["id"], body.enabled, STUDYCHAT_POLICY_VERSION, body.purpose.strip(), now()))
    return {"enabled": body.enabled, "policy_version": STUDYCHAT_POLICY_VERSION}


@router.post("/datasets/{source_id}/studychat-pilot", status_code=202)
def studychat_pilot(request: Request, user: User, source_id: str, body: StudyChatPilotRequest):
    from .jobs import active_config
    from .studychat_policy import is_snapshot, require_deepseek

    require_role(user, "teacher", "admin")
    settings = request.app.state.settings
    with database(settings, write=True) as db:
        source = get_source(db, user, source_id, "evaluate")
        if not is_snapshot(source):
            raise Problem(422, "NOT_STUDYCHAT", "抽样试跑仅适用于 StudyChat 快照。")
        current = db.execute("SELECT json_extract(settings,'$.rubric_version') FROM evaluation_configs WHERE id=?",
                             (active_config(db),)).fetchone()[0]
        if is_five_dimension(current):
            raise Problem(409, "STUDYCHAT_CHECK_REQUIRED", "请从数据集评价先检查质量，再使用授权模型生成报告。")
        if body.method == "hybrid":
            require_deepseek(db, settings, source_id)
        rows = db.execute("""SELECT s.*,t.title,
               (SELECT COUNT(*) FROM events WHERE session_id=s.id AND type='chat_user_message') AS turns
               FROM sessions s JOIN task_definitions t ON t.id=s.task_id
               WHERE s.source_id=? ORDER BY t.title,s.external_key_hash""", (source_id,))
        groups = {}
        for row in rows:
            groups.setdefault(row["title"], []).append(row)
        preferred = [term + " · A" + str(task) for term in ("F24", "S25") for task in (2, 3, 4)]
        groups_order = [key for key in preferred if key in groups] + [key for key in groups if key not in preferred]
        sessions = [next((row for row in groups[key] if row["turns"] >= 4), groups[key][0]) for key in groups_order[:6]]
        if not sessions:
            raise Problem(422, "NO_STUDYCHAT_SESSIONS", "请先导入 StudyChat 会话。")
        return public_job(db, schedule_evaluation(db, user["id"], sessions, body.method, "research", source_id=source_id))


@router.get("/datasets/builtins/studychat")
def builtin_catalog(request: Request, user: User):
    from .builtin_studychat import IDENTITY, manifest

    require_role(user, "teacher", "admin")
    bundle = manifest()
    with database(request.app.state.settings) as db:
        source = next((row for row in db.execute("SELECT * FROM data_sources WHERE source_identity=? AND source_version=?",
                                                (IDENTITY, bundle["sha256"])) if source_access(db, user, row)), None)
    return {"sessions": bundle["preview"]["valid"], "stats": bundle["preview"]["snapshot"]["stats"],
            "source_id": source["id"] if source else None}


@router.post("/datasets/imports/studychat-builtin", status_code=202)
def stage_studychat_builtin(request: Request, user: User, body: StudyChatBuiltinRequest):
    from .builtin_studychat import IDENTITY, LICENCE, manifest

    require_role(user, "teacher", "admin")
    bundle = manifest()
    data = body.model_dump(mode="json") | {"license_note": LICENCE, "source_identity": IDENTITY}
    with database(request.app.state.settings, write=True) as db:
        job, _ = enqueue(db, user["id"], "studychat_preview", {"builtin": True, "metadata": data,
                                                               "revision": bundle["sha256"]}, 1)
        return public_job(db, job)


@router.post("/datasets/imports/studychat-local", status_code=202)
def stage_studychat(request: Request, user: User, body: StudyChatLocalRequest):
    from .studychat_snapshot import matching_dialogue_files, snapshot_root

    require_role(user, "teacher", "admin")
    data = body.model_dump(mode="json")
    order = {key: data.pop(key) for key in ("semester", "assignment")}
    data["source_identity"] = "studychat-v1.1"
    root = snapshot_root()
    terms = ("f24", "s25") if order["semester"] == "all" else (order["semester"],)
    matching_dialogue_files(root, terms)
    local_files = [file for folder in ("dialogues", "scores", "assignment_text", "submissions")
                   for file in (root / folder).rglob("*") if file.is_file()]
    if (root / "data.jsonl").is_file():
        local_files.append(root / "data.jsonl")
    revision = digest(dump(sorted((str(file.relative_to(root)), file.stat().st_size, file.stat().st_mtime_ns)
                                  for file in local_files)))
    with database(request.app.state.settings, write=True) as db:
        job, _ = enqueue(db, user["id"], "studychat_preview", {"order": order, "metadata": data, "revision": revision}, 1)
        return public_job(db, job)


@router.post("/datasets/imports", status_code=201)
def upload(request: Request, user: User, adapter: Annotated[str, Form()],
           metadata: Annotated[str, Form()] = "{}", file: Annotated[UploadFile | None, File()] = None):
    require_role(user, "teacher", "admin")
    if len(metadata) > 8192:
        raise Problem(422, "IMPORT_METADATA", "导入元数据过长。")
    try:
        meta = ImportMetadata.model_validate_json(metadata).model_dump(mode="json")
    except ValidationError:
        raise Problem(422, "IMPORT_METADATA", "导入元数据格式不正确。") from None
    if file and file.size is not None and file.size > MAX_UPLOAD:
        raise Problem(413, "FILE_TOO_LARGE", "单个导入文件最大 100 MB。")
    data = file.file.read(MAX_UPLOAD + 1) if file else b""
    if len(data) > MAX_UPLOAD:
        raise Problem(413, "FILE_TOO_LARGE", "单个导入文件最大 100 MB。")
    if adapter != "demo" and not file:
        raise Problem(422, "MISSING_FILE", "请选择需要导入的文件。")
    if adapter == "demo":
        meta.update(license_note="仓库内合成数据，限工程演示", intended_use="工程演示",
                    retain_until=meta["retain_until"] or "2035-12-31")
    settings = request.app.state.settings
    records, preview = prepare_import(settings, adapter, meta, data, (file.filename or "") if file else "")
    input_hash = digest(dump([adapter, meta]))
    with database(settings, write=True) as db:
        if meta["mapped_task_id"]:
            task = db.execute("SELECT * FROM task_definitions WHERE id=? AND status='published'", (meta["mapped_task_id"],)).fetchone()
            if adapter == "demo" or not task or (user["role"] != "admin" and task["owner_user_id"] != user["id"]
                                                and not course_access(db, user, task["course_id"], teacher=True)):
                raise Problem(403, "TASK_MAPPING_FORBIDDEN", "请选择有权关联的已发布任务版本。")
            preview["mapped_task"] = {"title": task["title"], "version": task["version"]}
        else:
            previous = {}
            for record in records:
                task = record["task"]
                existing = db.execute(
                    """SELECT DISTINCT json_extract(metadata,'$.instructions_hash') AS value FROM task_definitions
                       WHERE status='source' AND json_extract(metadata,'$.source_identity')=?
                       AND json_extract(metadata,'$.external_task_id')=?""",
                    (meta["source_identity"], task["external_task_id"]),
                )
                values = previous.setdefault(task["external_task_id"], {row["value"] for row in existing})
                values.add(digest(task["instructions"]))
            preview["task_conflicts"] = sum(len(values) > 1 for values in previous.values())
        batch = db.execute("SELECT id,preview FROM import_batches WHERE owner_user_id=? AND input_hash=?",
                           (user["id"], input_hash)).fetchone()
        if batch:
            return {"id": batch["id"], **json.loads(batch["preview"])}
        batch_id = uid()
        db.execute("INSERT INTO import_batches VALUES (?,?,?,?,?,?,?,?)",
                   (batch_id, user["id"], adapter, dump(preview), dump(records), dump(meta), input_hash, now()))
    return {"id": batch_id, **preview}


def get_batch(db, user, batch_id):
    require_role(user, "teacher", "admin")
    batch = row_or_404(db.execute("SELECT * FROM import_batches WHERE id=?", (batch_id,)).fetchone())
    if batch["owner_user_id"] != user["id"]:
        raise Problem(403, "IMPORT_FORBIDDEN", "您没有该导入暂存记录的操作权限。")
    return batch


@router.get("/datasets/imports/{batch_id}/preview")
def preview(request: Request, user: User, batch_id: str):
    with database(request.app.state.settings) as db:
        batch = get_batch(db, user, batch_id)
        return {"id": batch_id, **json.loads(batch["preview"])}


@router.post("/datasets/imports/{batch_id}/commit", status_code=202)
def commit_import(request: Request, user: User, batch_id: str, body: ConfirmImport):
    with database(request.app.state.settings, write=True) as db:
        batch = get_batch(db, user, batch_id)
        metadata, preview_data = json.loads(batch["metadata"]), json.loads(batch["preview"])
        if not body.license_confirmed or any(not (metadata.get(field) or "").strip()
                                             for field in ("license_note", "intended_use", "retain_until")):
            raise Problem(422, "LICENSE_REQUIRED", "请填写用途、许可和保留期限，并确认导入许可。")
        if not preview_data["valid"] or (preview_data["errors"] and not body.only_valid):
            raise Problem(422, "IMPORT_HAS_ERRORS", "请修正错误记录，或明确选择仅导入有效记录。")
        job, _ = enqueue(db, user["id"], "import", {"batch_id": batch_id}, 1)
        return public_job(db, job)


@router.get("/datasets")
def list_datasets(request: Request, user: User, page: Page = 1, page_size: PageSize = 20,
                  sort: Literal["newest", "oldest"] = "newest"):
    require_role(user, "teacher", "admin")
    with database(request.app.state.settings) as db:
        order = "DESC" if sort == "newest" else "ASC"
        items = []
        for source in db.execute(f"""SELECT ds.*, (SELECT COUNT(*) FROM sessions WHERE source_id=ds.id) AS session_count
                                     FROM data_sources ds ORDER BY ds.created_at {order}"""):
            if source_access(db, user, source):
                items.append({**dict(source), "completeness": json.loads(source["completeness"])})
        return page_result(items, page, page_size)


@router.get("/datasets/{source_id}")
def dataset_detail(request: Request, user: User, source_id: str):
    from .studychat_policy import SCOPE, consented, is_snapshot

    with database(request.app.state.settings) as db:
        source = get_source(db, user, source_id)
        studychat = is_snapshot(source)
        return {**dict(source), "completeness": json.loads(source["completeness"]),
                "can_evaluate": source_access(db, user, source, "evaluate"),
                "can_export": source_access(db, user, source, "export"),
                "studychat_snapshot": studychat,
                "deepseek_consented": consented(db, source_id) if studychat else False,
                "deepseek_scope": SCOPE if studychat else None,
                "can_authorize_deepseek": studychat and user["role"] == "admin",
                "can_delete": user["role"] == "admin" or bool(source["demo"] and source["owner_user_id"] == user["id"])}


@router.get("/datasets/{source_id}/results")
def dataset_results(request: Request, user: User, source_id: str, page: Page = 1, page_size: PageSize = 20,
                    sort: Literal["newest", "oldest"] = "newest", method: str = "",
                    dimension: DimensionName | None = None, rubric: Literal["five", "legacy"] | None = None,
                    level: Annotated[int | None, Query(ge=0, le=5)] = None,
                    state: Literal["scored", "not_applicable", "insufficient_evidence"] | None = None,
                    process_min: Annotated[float | None, Query(ge=0, le=100)] = None,
                    outcome_min: Annotated[float | None, Query(ge=0, le=100)] = None,
                    diagnosis: str = "", reviewed: bool | None = None):
    from .studychat_policy import is_snapshot

    with database(request.app.state.settings) as db:
        studychat = is_snapshot(get_source(db, user, source_id))
        order = "DESC" if sort == "newest" else "ASC"
        if rubric == "legacy":
            rows = db.execute(f"""WITH archive AS (
                SELECT rv.*, s.metadata AS session_metadata, ROW_NUMBER() OVER (PARTITION BY rv.session_id,rv.method,rv.purpose,
                    json_extract(rv.body,'$.rubric_version') ORDER BY er.report_id IS NULL,rv.created_at DESC,rv.rowid DESC) AS ordering
                FROM report_versions rv JOIN sessions s ON s.id=rv.session_id
                LEFT JOIN effective_reports er ON er.report_id=rv.id WHERE s.source_id=?
                AND NOT EXISTS (SELECT 1 FROM studychat_quality_rejections q WHERE q.report_id=rv.id)
                AND json_extract(rv.body,'$.rubric_version') NOT IN (?,?) AND COALESCE(json_extract(rv.body,'$.review.draft'),0)=0)
                SELECT * FROM archive WHERE ordering=1 ORDER BY created_at {order}""", (source_id, *FIVE_DIMENSION_VERSIONS))
        else:
            rows = db.execute(f"""SELECT rv.*,s.metadata AS session_metadata FROM effective_reports er JOIN report_versions rv ON rv.id=er.report_id
                                  JOIN sessions s ON s.id=rv.session_id WHERE s.source_id=? ORDER BY rv.created_at {order}""",
                              (source_id,))
        items = []
        for row in rows:
            record = report_outcomes(db, json.loads(row["body"]), json.loads(row["session_metadata"]), studychat)
            outcome = record["display_outcome_score"]
            legacy = not is_five_dimension(record["rubric_version"])
            if ((method and method != row["method"]) or
                (rubric and legacy != (rubric == "legacy")) or
                (process_min is not None and (record["process_score"] is None or record["process_score"] < process_min)) or
                (outcome_min is not None and (outcome is None or outcome < outcome_min)) or
                (diagnosis and diagnosis != (record["diagnosis"]["label"] or "none")) or
                (reviewed is not None and reviewed != (row["kind"] == "reviewed"))):
                continue
            if (dimension or level is not None or state) and not any(
                (not dimension or item["dimension"] == dimension) and
                (level is None or item["level"] == level) and (not state or item["state"] == state)
                for item in record["dimensions"]
            ):
                continue
            items.append({"id": row["id"], "run_id": row["run_id"], "session_id": row["session_id"],
                          "method": row["method"], "process_score": record["process_score"], "outcome_score": outcome,
                          "process_score_five": record.get("process_score_five", five_point_score(record["process_score"])),
                          **{key: record[key] for key in ("source_outcome_score", "source_outcome_percent", "system_outcome_score", "outcome_basis")},
                          "rubric_version": record["rubric_version"], "legacy_rubric": legacy,
                          "diagnosis": record["diagnosis"]["label"], "reviewed": row["kind"] == "reviewed",
                          "dimensions": [{key: item[key] for key in ("dimension", "state", "level")} for item in record["dimensions"]]})
        return page_result(items, page, page_size)


def session_detail(db, session):
    metadata = json.loads(session["metadata"])
    task = db.execute("SELECT title,version FROM task_definitions WHERE id=?", (session["task_id"],)).fetchone()
    participant = db.execute("SELECT substr(external_key_hash,1,12) FROM participants WHERE id=?",
                             (session["participant_id"],)).fetchone()
    return {**{key: session[key] for key in ("id", "source_type", "source_id", "task_id", "status", "created_at")},
            "task_title": task["title"], "task_version": task["version"],
            "participant": participant[0] if participant else None, "coverage": metadata.get("coverage"),
            "fixture_id": metadata.get("fixture_id")}


@router.get("/sessions")
def list_sessions(request: Request, user: User, page: Page = 1, page_size: PageSize = 20, source_id: str | None = None,
                  source_type: Literal["dataset", "live"] | None = None,
                  sort: Literal["newest", "oldest"] = "newest", task: str = "", participant: str = "",
                  coverage: str = "", since: str = "", until: str = ""):
    with database(request.app.state.settings) as db:
        if source_id:
            get_source(db, user, source_id)
        order = "DESC" if sort == "newest" else "ASC"
        found = db.execute(f"""SELECT * FROM sessions WHERE (? IS NULL OR source_id=?)
                               AND (? IS NULL OR source_type=?) ORDER BY created_at {order},rowid {order}""",
                           (source_id, source_id, source_type, source_type))
        items = []
        for session in found:
            try:
                get_session(db, user, session["id"])
            except Problem as error:
                if error.status == 403:
                    continue
                raise
            item = session_detail(db, session)
            if (task.casefold() in item["task_title"].casefold() and participant in (item["participant"] or "")
                and (not coverage or coverage == item["coverage"])
                and (not since or item["created_at"][:10] >= since) and (not until or item["created_at"][:10] <= until)):
                items.append(item)
        return page_result(items, page, page_size)


@router.get("/sessions/{session_id}")
def read_session(request: Request, user: User, session_id: str):
    from .evaluation import event_payloads
    from .studychat_policy import is_snapshot
    from .studychat_quality import session_quality

    with database(request.app.state.settings) as db:
        session = get_session(db, user, session_id)
        source = db.execute("SELECT * FROM data_sources WHERE id=?", (session["source_id"],)).fetchone()
        studychat = is_snapshot(source)
        if studychat and user["role"] != "student":
            report_ids = db.execute("""WITH versions AS (
                SELECT r.id AS report_id,r.created_at,ROW_NUMBER() OVER (
                    PARTITION BY r.method,r.purpose,json_extract(r.body,'$.rubric_version')
                    ORDER BY er.report_id IS NULL,r.created_at DESC,r.rowid DESC) AS ordering
                FROM report_versions r LEFT JOIN effective_reports er ON er.report_id=r.id
                WHERE r.session_id=? AND COALESCE(json_extract(r.body,'$.review.draft'),0)=0
                AND NOT EXISTS (SELECT 1 FROM studychat_quality_rejections q WHERE q.report_id=r.id))
                SELECT report_id FROM versions WHERE ordering=1 ORDER BY created_at DESC""", (session_id,))
        else:
            report_ids = db.execute(
                "SELECT report_id FROM effective_reports WHERE session_id=? AND (? OR released_to_student=1)",
                (session_id, user["role"] != "student"),
            )
        reports = []
        for record in report_ids:
            report = get_report(db, user, record["report_id"])
            reports.append({key: report[key] for key in ("id", "run_id", "method", "purpose", "status", "version",
                                                         "kind", "process_score", "process_score_five", "diagnosis", "conflicts", "model",
                                                         "provider", "rubric_version", "legacy_rubric", "source_outcome_score",
                                                         "source_outcome_percent", "system_outcome_score", "outcome_basis")}
                           | {"effective": report["effective"], "outcome_score": report["display_outcome_score"]})
        jobs = [public_job(db, job) for job in db.execute(
            "SELECT * FROM jobs WHERE session_id=? ORDER BY rowid DESC LIMIT 4", (session_id,)
        )]
        task = db.execute("SELECT title,instructions FROM task_definitions WHERE id=?", (session["task_id"],)).fetchone()
        grades = json.loads(session["metadata"]).get("external_scores", {})
        facts = None
        if studychat:
            term, _, assignment = task["title"].partition(" · ")
            turn_count, message_count = db.execute("""SELECT count(CASE WHEN type='chat_user_message' THEN 1 END), count(*)
                FROM events WHERE session_id=? AND type IN ('chat_user_message','chat_ai_message')""", (session_id,)).fetchone()
            submissions = db.execute("SELECT count(*) FROM artifacts WHERE session_id=?", (session_id,)).fetchone()[0]
            quality = session_quality(db, session, task, event_payloads(db, session_id, (1 << 63) - 1))
            latest = db.execute("""SELECT q.body FROM studychat_run_quality q JOIN evaluation_runs r ON r.id=q.run_id
                WHERE r.session_id=? AND (q.provider_response_valid=1 OR json_extract(r.error,'$.code')='MODEL_TASK_MISMATCH')
                ORDER BY r.rowid DESC LIMIT 1""", (session_id,)).fetchone()
            if latest:
                model_quality = json.loads(latest["body"])
                if model_quality["task_relevance"] != "relevant":
                    quality = model_quality
            grade = next((report["source_outcome_score"] for report in reports if report["source_outcome_score"] is not None),
                         None) if quality["grade_link_valid"] else None
            grades = grades if grade is not None else {}
            title = next((re.sub(r"(?<!\*)\*{1,3}([^*]+)\*{1,3}(?!\*)", r"\1", line.removeprefix("# ").strip())
                          for line in task["instructions"].splitlines() if line.startswith("# ")), task["title"])
            facts = {"term": term, "assignment": assignment, "turn_count": turn_count, "message_count": message_count,
                     "assignment_title": title, "submission_count": submissions, "grade_matched": quality["grade_matched"],
                     "quality": quality,
                     **outcome_fields(source_score=grade, studychat=True)}
        return {**session_detail(db, session), "reports": reports, "jobs": jobs,
                "task_instructions": task["instructions"], "studychat": facts, "external_scores": grades}


@router.get("/datasets/{source_id}/excluded-reports")
def excluded_reports(request: Request, user: User, source_id: str, page: Page = 1, page_size: PageSize = 20):
    require_role(user, "teacher", "admin")
    with database(request.app.state.settings) as db:
        get_source(db, user, source_id)
        rows = db.execute("""SELECT q.report_id,r.session_id,r.run_id,q.reason,t.title
            FROM studychat_quality_rejections q JOIN report_versions r ON r.id=q.report_id
            JOIN sessions s ON s.id=r.session_id JOIN task_definitions t ON t.id=s.task_id
            WHERE s.source_id=? ORDER BY q.created_at DESC,r.rowid DESC""", (source_id,))
        return page_result([dict(row) | {"status": "quality_rejected", "reason": json.loads(row["reason"])} for row in rows],
                           page, page_size)


@router.get("/sessions/{session_id}/dialogue")
def dialogue(request: Request, user: User, session_id: str, page: Page = 1, page_size: PageSize = 20, event_id: str = ""):
    where = "session_id=? AND type IN ('chat_user_message','chat_ai_message')"
    with database(request.app.state.settings) as db:
        get_session(db, user, session_id)
        total = db.execute(f"SELECT count(*) FROM events WHERE {where}", (session_id,)).fetchone()[0]
        if event_id:
            event = row_or_404(db.execute(f"SELECT sequence_no FROM events WHERE {where} AND id=?",
                                          (session_id,event_id)).fetchone())
            offset = db.execute(f"SELECT count(*) FROM events WHERE {where} AND sequence_no<?",
                                (session_id,event["sequence_no"])).fetchone()[0]
            page = offset // page_size + 1
        items = []
        for event in db.execute(f"""SELECT id,type,sequence_no,payload, SUM(type='chat_user_message') OVER (
                    ORDER BY sequence_no) AS ordinal FROM events WHERE {where} ORDER BY sequence_no LIMIT ? OFFSET ?""",
                                (session_id,page_size,(page-1)*page_size)):
            payload = json.loads(event["payload"])
            items.append({"id": event["id"], "sequence_no": event["sequence_no"], "turn": event["ordinal"],
                          "conversation": payload.get("conversation"), "source_turn": payload.get("source_turn"),
                          "role": "student" if event["type"] == "chat_user_message" else "ai", "text": payload["text"]})
        return {"items": items, "page": page, "page_size": page_size, "total": total}


@router.get("/sessions/{session_id}/timeline")
def timeline(request: Request, user: User, session_id: str, page: Page = 1, page_size: PageSize = 20,
             sort: Literal["oldest", "newest"] = "oldest"):
    with database(request.app.state.settings) as db:
        get_session(db, user, session_id)
        total = db.execute("SELECT COUNT(*) FROM events WHERE session_id=?", (session_id,)).fetchone()[0]
        order = "ASC" if sort == "oldest" else "DESC"
        items = []
        for event in db.execute(f"SELECT * FROM events WHERE session_id=? ORDER BY sequence_no {order} LIMIT ? OFFSET ?",
                                (session_id, page_size, (page - 1) * page_size)):
            value = dict(event)
            value["payload"] = json.loads(value["payload"])
            items.append({key: value[key] for key in ("id", "type", "occurred_at", "timestamp_precision", "sequence_no")}
                         | {"excerpt": short_excerpt(value)})
        return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/sessions/{session_id}/events/{event_id}")
def event_detail(request: Request, user: User, session_id: str, event_id: str):
    with database(request.app.state.settings) as db:
        get_session(db, user, session_id)
        event = row_or_404(db.execute("SELECT * FROM events WHERE id=? AND session_id=?", (event_id, session_id)).fetchone())
        return {**dict(event), "payload": json.loads(event["payload"])}


@router.get("/sessions/{session_id}/artifacts")
def artifacts(request: Request, user: User, session_id: str, page: Page = 1, page_size: PageSize = 20):
    with database(request.app.state.settings) as db:
        get_session(db, user, session_id)
        total = db.execute("SELECT COUNT(*) FROM artifacts WHERE session_id=?", (session_id,)).fetchone()[0]
        items = db.execute("""SELECT id,kind,version,checksum,mime_type FROM artifacts WHERE session_id=?
                              ORDER BY kind,version LIMIT ? OFFSET ?""", (session_id, page_size, (page - 1) * page_size))
        return {"items": [dict(row) for row in items], "total": total, "page": page, "page_size": page_size}


@router.get("/sessions/{session_id}/artifacts/{artifact_id}")
def artifact_detail(request: Request, user: User, session_id: str, artifact_id: str):
    with database(request.app.state.settings) as db:
        get_session(db, user, session_id)
        row = row_or_404(db.execute("SELECT * FROM artifacts WHERE id=? AND session_id=?", (artifact_id, session_id)).fetchone())
        content = row["content"]
        return {**dict(row), "content": content[:50_000], "preview_truncated": len(content) > 50_000}


@router.post("/evaluation-runs/batch", status_code=202)
def batch_evaluate(request: Request, user: User, body: BatchRequest):
    from .studychat_policy import is_snapshot, require_deepseek

    require_role(user, "teacher", "admin")
    with database(request.app.state.settings, write=True) as db:
        source = get_source(db, user, body.source_id, "evaluate")
        if is_snapshot(source):
            raise Problem(409, "STUDYCHAT_CHECK_REQUIRED", "StudyChat 会话须通过现场数据质量检查。")
        sessions = [get_session(db, user, session_id, "evaluate") for session_id in sorted(set(body.session_ids))]
        if any(session["source_id"] != source["id"] or session["status"] != "submitted" for session in sessions):
            raise Problem(422, "SESSION_SCOPE", "请选择当前数据源中已提交的记录。")
        if body.method == "demo_conflict" and (not source["demo"] or any(
            json.loads(session["metadata"]).get("fixture_id") != "F5" for session in sessions
        )):
            raise Problem(422, "FIXTURE_SCOPE", "F5C 冲突演示只可用于 F5 演示记录。")
        job = schedule_evaluation(db, user["id"], sessions, body.method, "demo" if source["demo"] else "research",
                                  source_id=source["id"])
        return public_job(db, job)


@router.get("/jobs/{job_id}")
def job_detail(request: Request, user: User, job_id: str):
    with database(request.app.state.settings) as db:
        return public_job(db, get_job(db, user, job_id))


@router.post("/jobs/{job_id}/cancel")
def cancel_job(request: Request, user: User, job_id: str):
    with database(request.app.state.settings, write=True) as db:
        job = get_job(db, user, job_id, "evaluate")
        if job["owner_user_id"] != user["id"] and user["role"] != "admin":
            raise Problem(403, "JOB_FORBIDDEN", "仅创建者或管理员可以取消任务。")
        if job["status"] == "queued":
            db.execute("UPDATE jobs SET status='cancelled' WHERE id=?", (job_id,))
            db.execute("UPDATE evaluation_runs SET status='cancelled' WHERE job_id=? AND status='queued'", (job_id,))
        elif job["status"] == "running":
            db.execute("UPDATE jobs SET status='cancel_requested' WHERE id=?", (job_id,))
        return public_job(db, db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())


@router.post("/jobs/{job_id}/retry-failed", status_code=202)
def retry_failed(request: Request, user: User, job_id: str):
    with database(request.app.state.settings, write=True) as db:
        job = get_job(db, user, job_id, "evaluate")
        if job["owner_user_id"] != user["id"] and user["role"] != "admin":
            raise Problem(403, "JOB_FORBIDDEN", "仅创建者或管理员可以重试任务。")
        if db.execute("SELECT 1 FROM analysis_batches WHERE job_id=?", (job_id,)).fetchone():
            raise Problem(409, "ANALYSIS_NEW_RUN", "请返回分析流程重新确认范围和调用上限，再创建新的分析任务。")
        is_sample = db.execute("SELECT 1 FROM studychat_sample_members WHERE job_id=? LIMIT 1", (job_id,)).fetchone()
        if job["status"] not in ("partial", "failed") or job["retry_count"] >= (4 if is_sample else 2):
            raise Problem(409, "RETRY_UNAVAILABLE", "当前任务没有可重试的失败项，或已达到重试次数上限。")
        if job["kind"] == "evaluation":
            retryable = []
            sample_errors = {"MODEL_REPORT_EVIDENCE", "MODEL_SEGMENT_EVIDENCE", "PROVIDER_TRANSPORT", "PROVIDER_CIRCUIT",
                             "PROVIDER_EMPTY", "PROVIDER_RATE_LIMIT"} if is_sample else set()
            for run in db.execute("SELECT id,error FROM evaluation_runs WHERE job_id=? AND status='failed'", (job_id,)):
                if not run["error"] or json.loads(run["error"])["code"] in {"EVALUATION_FAILED", "REPORT_FAILED", *sample_errors}:
                    retryable.append((run["id"],))
            if not retryable:
                raise Problem(409, "RETRY_UNAVAILABLE", "没有可重试的记录。")
            db.executemany("UPDATE evaluation_runs SET status='queued',error=NULL WHERE id=?", retryable)
            count = len(retryable)
        else:
            code = json.loads(job["error"])["code"] if job["error"] else None
            if not job["result"] or code not in ("JOB_FAILED", "WORKER_LEASE_LOST"):
                raise Problem(409, "RETRY_UNAVAILABLE", "当前失败无法重试，请检查输入和权限。")
            count = job["failed_count"]
        db.execute("""UPDATE jobs SET status='queued', error=NULL, available_at=0, retry_count=retry_count+1,
                      failed_count=MAX(0,failed_count-?),progress_done=MAX(0,progress_done-?) WHERE id=?""",
                   (count, count, job_id))
        return public_job(db, db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())


@router.get("/evaluation-runs/{run_id}")
def evaluation_detail(request: Request, user: User, run_id: str, report_id: str | None = None):
    with database(request.app.state.settings) as db:
        run = get_run(db, user, run_id)
        if report_id:
            report = get_report(db, user, report_id)
            if report["run_id"] != run_id:
                raise Problem(404, "NOT_FOUND", "找不到对应报告版本。")
            return report
        row = db.execute(
            """SELECT rv.id FROM report_versions rv LEFT JOIN effective_reports er ON er.report_id=rv.id
               WHERE rv.run_id=? AND (? OR er.released_to_student=1)
               ORDER BY er.report_id IS NULL,rv.version DESC LIMIT 1""", (run_id, user["role"] != "student")
        ).fetchone()
        if row:
            return get_report(db, user, row["id"])
        return {"run_id": run_id, "session_id": run["session_id"], "status": run["status"],
                "error": json.loads(run["error"]) if run["error"] else None}


@router.get("/annotations/{annotation_id}/evidence")
def annotation_evidence(request: Request, user: User, annotation_id: str):
    with database(request.app.state.settings) as db:
        annotation = row_or_404(db.execute("SELECT * FROM annotations WHERE id=?", (annotation_id,)).fetchone())
        get_report(db, user, annotation["report_id"])
        return {"items": [dict(row) for row in db.execute(
            """SELECT el.event_id,el.excerpt,e.type,e.occurred_at,e.sequence_no FROM evidence_links el
               JOIN events e ON e.id=el.event_id WHERE el.annotation_id=? ORDER BY e.sequence_no""", (annotation_id,)
        )]}
