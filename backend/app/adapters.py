import json
import re
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints, ValidationError, field_validator, model_validator

from .core import Problem, digest, dump, now, pseudonym, uid

MAX_UPLOAD = 100 * 1024 * 1024
Text = Annotated[str, StringConstraints(max_length=1_048_576)]
Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
EVENT_TYPES = {
    "task_opened", "chat_user_message", "chat_ai_message", "code_snapshot", "run_started",
    "run_finished", "test_finished", "artifact_saved", "submission_created", "task_closed", "editor_focus",
}
EXECUTION_FIELDS = {"artifact_id", "artifact_version", "artifact_checksum", "execution_mode", "verified", "synthetic"}


class Event(BaseModel):
    external_event_id: Identifier
    sequence_no: int = Field(strict=True, ge=1)
    occurred_at: datetime | None = None
    type: Identifier
    payload: dict

    @field_validator("occurred_at")
    @classmethod
    def timezone_required(cls, value):
        if value is not None and value.utcoffset() is None:
            raise ValueError("事件时间必须包含时区")
        return value


class Code(BaseModel):
    language: Identifier
    content: Text
    version: int = Field(strict=True, ge=1)


class RunPayload(BaseModel):
    artifact_id: Identifier
    artifact_version: int = Field(strict=True, ge=1)
    artifact_checksum: str = Field(pattern=r"^[a-f0-9]{64}$")
    execution_mode: Literal["sandbox", "fixture_simulation", "external_unknown"]
    synthetic: bool = Field(strict=True)
    verified: bool = Field(strict=True)

    @model_validator(mode="after")
    def execution_consistent(self):
        if self.execution_mode == "fixture_simulation" and not self.synthetic:
            raise ValueError("fixture 模拟必须标记 synthetic")
        if self.execution_mode == "external_unknown" and self.verified:
            raise ValueError("来源未知的执行不能标记为已验证")
        return self


class RunFinished(RunPayload):
    exit_code: int = Field(strict=True)
    stdout: Text
    stderr: Text


class TestFinished(RunPayload):
    passed: int = Field(strict=True, ge=0)
    failed: int = Field(strict=True, ge=0)
    cases: list[dict] = Field(max_length=1000)

    @model_validator(mode="after")
    def counts_consistent(self):
        if self.verified and self.cases:
            if any(not isinstance(case.get("id"), str) or type(case.get("passed")) is not bool for case in self.cases):
                raise ValueError("已核验测试应有用例标识和明确测试结果")
            if (len({case["id"] for case in self.cases}) != len(self.cases)
                or self.passed != sum(case["passed"] for case in self.cases)
                or self.failed != len(self.cases) - self.passed):
                raise ValueError("测试用例与汇总数量不一致")
        return self


class Artifact(BaseModel):
    external_artifact_id: Identifier
    kind: Identifier
    version: int = Field(strict=True, ge=1)
    content: Text
    mime_type: Identifier


class Task(BaseModel):
    external_task_id: Identifier
    title: Text = "未命名来源任务"
    instructions: Text = ""


class Record(BaseModel):
    external_session_id: Identifier
    participant_key: Identifier
    task: Task
    events: list[Event] = Field(min_length=1, max_length=20_000)
    artifacts: list[Artifact] = Field(default_factory=list, max_length=500)
    external_scores: dict[str, float] = Field(default_factory=dict)


def read_json(data, filename):
    try:
        decoded = data.decode("utf-8-sig")
        if filename.lower().endswith(".jsonl"):
            return {"schema_version": "1.0", "source": {}, "sessions": [
                json.loads(line) for line in decoded.splitlines() if line.strip()
            ]}
        if not filename.lower().endswith(".json"):
            raise Problem(422, "FILE_TYPE", "请上传 UTF-8 JSON 或 JSONL 文件。")
        return json.loads(decoded)
    except (UnicodeError, ValueError, RecursionError):
        raise Problem(422, "INVALID_JSON", "文件无法解析为 UTF-8 JSON。") from None


def load_demo():
    from .fixtures import checked_fixture

    sessions = [checked_fixture(f"F{number}_input.json")["sessions"][0] for number in range(1, 7)]
    return {"schema_version": "1.0", "source": {"name": "校园商店销售分析 · 演示集"}, "sessions": sessions}


def inspect_payload(event, artifacts):
    payload = event.payload
    if len(dump(payload).encode("utf-8")) > 4_194_304:
        raise ValueError("事件字段总大小超限")
    if event.type in ("chat_user_message", "chat_ai_message"):
        if not isinstance(payload.get("text"), str) or len(payload["text"].encode()) > 1_048_576:
            raise ValueError("对话需提供文本且不超过 1 MB")
    elif event.type == "code_snapshot":
        payload.update(Code.model_validate(payload).model_dump())
    elif event.type in ("run_finished", "test_finished"):
        if EXECUTION_FIELDS <= payload.keys():
            schema = RunFinished if event.type == "run_finished" else TestFinished
            payload.update(schema.model_validate(payload).model_dump())
            artifact = artifacts.get(payload["artifact_id"])
            if payload["verified"] and (not artifact or artifact.kind != "code"
                    or artifact.version != payload["artifact_version"] or digest(artifact.content) != payload["artifact_checksum"]):
                payload.update(verified=False, verification_error="UNALIGNED_ARTIFACT_VERSION")
        else:
            payload.update(execution_mode="external_unknown", verified=False, synthetic=False)
    elif event.type == "submission_created":
        refs = payload.get("artifact_refs")
        if not isinstance(refs, list) or not all(isinstance(ref, str) for ref in refs):
            raise ValueError("提交需提供 artifact_refs")
        if any(ref not in artifacts for ref in refs):
            raise Problem(422, "ARTIFACT_REF_NOT_FOUND", "提交引用了记录中不存在的交付物。")
        payload.pop("artifact_checksum", None)
        payload.pop("artifact_version", None)
        code = [artifacts[ref] for ref in dict.fromkeys(refs) if artifacts[ref].kind == "code"]
        if len(code) == 1:
            payload["artifact_checksum"] = digest(code[0].content)
            payload["artifact_version"] = code[0].version


def normalize_record(candidate, key, source_identity, *, demo):
    try:
        parsed = Record.model_validate(candidate)
    except ValidationError as error:
        code = "MISSING_REQUIRED_FIELD" if any(e["type"] == "missing" for e in error.errors()) else "INVALID_RECORD"
        raise Problem(422, code, "会话必填字段缺失或格式不正确。") from None
    numbers = [event.sequence_no for event in parsed.events]
    if numbers != sorted(set(numbers)):
        raise Problem(422, "DUPLICATE_SEQUENCE", "事件序号必须唯一且严格递增。")
    if len({event.external_event_id for event in parsed.events}) != len(numbers):
        raise Problem(422, "DUPLICATE_EVENT", "同一会话中的事件标识必须唯一。")
    artifacts = {artifact.external_artifact_id: artifact for artifact in parsed.artifacts}
    if len(artifacts) != len(parsed.artifacts) or len({
        (artifact.kind, artifact.version) for artifact in parsed.artifacts
    }) != len(artifacts):
        raise Problem(422, "DUPLICATE_ARTIFACT", "同一会话中的交付物标识和类型版本必须唯一。")
    for event in parsed.events:
        try:
            inspect_payload(event, artifacts)
        except (ValueError, ValidationError):
            raise Problem(422, "INVALID_EVENT_PAYLOAD", "事件字段不符合规定格式。", {
                "sequence_no": event.sequence_no
            }) from None
        if event.type not in EVENT_TYPES:
            event.payload["original_type"] = event.type
            event.type = "custom"
    data = parsed.model_dump(mode="json")
    data["external_session_id"] = pseudonym(key, source_identity + "/session", parsed.external_session_id)
    data["participant_key"] = pseudonym(key, source_identity, parsed.participant_key)
    if demo:
        data["fixture_id"] = parsed.external_session_id
    return data


def prepare_import(settings, adapter, metadata, data, filename):
    demo = adapter == "demo"
    if demo:
        box = load_demo()
        data = dump(box).encode()
    elif adapter == "generic_json":
        box = read_json(data, filename)
    elif adapter == "studychat":
        from .studychat import adapt

        box = adapt(data, filename, metadata.get("mapping"))
    else:
        raise Problem(422, "ADAPTER_UNSUPPORTED", "不支持所选适配器。")
    return prepare_box(settings, adapter, metadata, box, digest(data))


def prepare_box(settings, adapter, metadata, box, source_version):
    demo = adapter == "demo"
    if not isinstance(box, dict) or box.get("schema_version") != "1.0":
        raise Problem(422, "SCHEMA_VERSION_UNSUPPORTED", "导入文件需要 Canonical JSON v1.0。")
    source = box.get("source")
    rows = box.get("sessions")
    if not isinstance(source, dict) or not isinstance(rows, list) or not rows:
        raise Problem(422, "MISSING_REQUIRED_FIELD", "文件必须包含来源元数据和至少一个会话。")
    name = metadata.get("name") or source.get("name")
    identity = metadata.get("source_identity") or ("collab-demo-v1" if demo else None)
    if not isinstance(name, str) or not name.strip() or len(name) > 160 or not isinstance(identity, str) or not re.fullmatch(
        r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,99}", identity
    ):
        raise Problem(422, "SOURCE_IDENTITY", "请填写数据源名称和稳定来源标识（字母、数字或 ._-）。")
    metadata.update(name=name.strip(), source_identity=identity, source_version=source_version, demo=demo)
    key = settings.key()
    valid, errors, seen = [], [], set()
    for index, candidate in enumerate(rows, start=1):
        try:
            if isinstance(candidate, dict) and isinstance(candidate.get("external_session_id"), str):
                external_key = pseudonym(key, identity + "/session", candidate["external_session_id"])
                if external_key in seen:
                    raise Problem(422, "DUPLICATE_IMPORT", "文件内存在重复会话标识。")
                seen.add(external_key)
            valid.append(normalize_record(candidate, key, identity, demo=demo))
        except Problem as error:
            errors.append({"record": index, "code": error.code, "message": error.message, "details": error.details})
    summaries = [record_summary(record) for record in valid]
    coverage = {label: sum(s["coverage"] == label for s in summaries) for label in
                ("process_and_outcome", "partial_process", "outcome_only", "none")}
    return valid, {"count": len(rows), "valid": len(valid), "errors": errors, "coverage": coverage,
                   "examples": summaries[:5], "adapter": adapter, "source": metadata["name"]}


def record_summary(record):
    types = {event["type"] for event in record["events"]}
    run = any(event["type"] in ("run_finished", "test_finished") and event["payload"].get("verified")
              and not event["payload"].get("synthetic") for event in record["events"])
    process = bool(types & {"chat_user_message", "chat_ai_message", "code_snapshot", "run_finished", "test_finished"})
    outcome = bool(record["artifacts"] and "submission_created" in types)
    coverage = ("process_and_outcome" if process and outcome and run and record.get("fixture_id") else
                "partial_process" if process else "outcome_only" if outcome else "none")
    missing = []
    if not record["task"]["instructions"]:
        missing.append("任务说明")
    if not run:
        missing.append("已验证执行记录")
    if not outcome:
        missing.append("提交物")
    custom = sum(event["type"] == "custom" for event in record["events"])
    return {"participant": record["participant_key"][:12], "task": record["task"]["title"],
            "events": len(record["events"]), "coverage": coverage, "missing": missing, "custom": custom,
            "event_types": sorted(types)}


def persist_import(db, owner_id, adapter, metadata, records):
    source = db.execute(
        "SELECT id FROM data_sources WHERE owner_user_id=? AND source_identity=? AND source_version=?",
        (owner_id, metadata["source_identity"], metadata["source_version"]),
    ).fetchone()
    if source:
        return {"source_id": source["id"], "duplicates": len(records)}
    source_id = uid()
    summaries = [record_summary(record) for record in records]
    db.execute("""INSERT INTO data_sources(id,name,adapter,owner_user_id,source_identity,source_version,completeness,
                 license_note,intended_use,retain_until,demo,created_at,allow_external_model)
                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
        source_id, metadata["name"], adapter, owner_id, metadata["source_identity"],
        metadata["source_version"], dump(summaries if len(summaries) < 6 else summaries[:5]),
        metadata["license_note"], metadata["intended_use"], metadata["retain_until"], metadata["demo"], now(),
        bool(metadata.get("allow_external_model")) and not metadata["demo"],
    ))
    for record in records:
        save_record(db, source_id, metadata, record)
    return {"source_id": source_id, "imported": len(records)}


def persist_snapshot_import(db, owner_id, metadata, preview, batch_id):
    source = db.execute("SELECT id FROM data_sources WHERE owner_user_id=? AND source_identity=? AND source_version=?",
                        (owner_id, metadata["source_identity"], metadata["source_version"])).fetchone()
    if source:
        return {"source_id": source["id"], "duplicates": preview["valid"]}
    source_id = uid()
    db.execute("""INSERT INTO data_sources(id,name,adapter,owner_user_id,source_identity,source_version,completeness,
                 license_note,intended_use,retain_until,demo,created_at,allow_external_model)
                 VALUES (?,?,?,?,?,?,?,?,?,?,0,?,0)""",
               (source_id, metadata["name"], "studychat", owner_id, metadata["source_identity"], metadata["source_version"],
                dump(preview["examples"]), metadata["license_note"], metadata["intended_use"], metadata["retain_until"], now()))
    for row in db.execute("SELECT body FROM import_batch_records WHERE batch_id=? ORDER BY ordinal", (batch_id,)):
        save_record(db, source_id, metadata, json.loads(row["body"]))
    return {"source_id": source_id, "imported": preview["valid"]}


def save_record(db, source_id, metadata, record):
    participant = db.execute(
        "SELECT id FROM participants WHERE source_id=? AND external_key_hash=?", (source_id, record["participant_key"])
    ).fetchone()
    participant_id = participant["id"] if participant else uid()
    if not participant:
        db.execute("INSERT INTO participants(id,source_id,external_key_hash) VALUES (?,?,?)",
                   (participant_id, source_id, record["participant_key"]))
    task = record["task"]
    task_key = digest(dump([metadata["source_identity"], task["external_task_id"], task["instructions"]]))
    task_id = metadata.get("mapped_task_id") or "source-" + task_key
    if not metadata.get("mapped_task_id"):
        db.execute(
            "INSERT INTO task_definitions VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING",
            (task_id, task_key, task["title"], task["instructions"], "[]", "{}",
             dump({"source_identity": metadata["source_identity"], "external_task_id": task["external_task_id"],
                   "instructions_hash": digest(task["instructions"])}), "source", None, None, task_key),
        )
    session_id = uid()
    db.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
        session_id, "dataset", source_id, participant_id, None, None, task_id, "submitted",
        record["external_session_id"], dump({"fixture_id": record.get("fixture_id"),
                                             "external_scores": record["external_scores"],
                                             "coverage": record_summary(record)["coverage"]}), now(),
    ))
    artifact_ids = {item["external_artifact_id"]: uid() for item in record["artifacts"]}
    for artifact in record["artifacts"]:
        db.execute("INSERT INTO artifacts VALUES (?,?,?,?,?,?,?)", (
            artifact_ids[artifact["external_artifact_id"]], session_id, artifact["kind"], artifact["version"], digest(artifact["content"]),
            artifact["content"], artifact["mime_type"],
        ))
    for event in record["events"]:
        stamp = event["occurred_at"]
        payload = event["payload"].copy()
        if event["type"] == "submission_created":
            payload["artifact_refs"] = [artifact_ids[key] for key in payload["artifact_refs"]]
        elif event["type"] in ("run_finished", "test_finished"):
            payload["artifact_id"] = artifact_ids.get(payload.get("artifact_id"))
        db.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?,?)", (
            uid(), session_id, event["sequence_no"], event["external_event_id"] if metadata["demo"] else uid(),
            event["type"], stamp, "source" if stamp else "sequence_only", dump(payload),
        ))
