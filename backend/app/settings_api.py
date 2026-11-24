import http.client
import json
from typing import Literal

from fastapi import Request
from pydantic import BaseModel, Field, SecretStr, model_validator

from .api import router
from .core import Problem, database, digest, dump, get_source, now, require_role, row_or_404, uid
from .evaluation import DemoProvider, active_config
from .http import User
from .live import public_task
from .deepseek_secret import save as save_deepseek_key
from .providers import DEEPSEEK_MODELS, DEEPSEEK_URL, deployment_key, provider_config, request_completion, validate_url
from .rubrics import FIVE_POINT_BARS

BARS = {name: dict(enumerate(anchors, 1)) for name, anchors in FIVE_POINT_BARS.items()}


class ProviderRequest(BaseModel):
    enabled: bool = False
    provider: Literal["openai-compatible", "deepseek"] = "openai-compatible"
    model: str = Field(default="", max_length=100)
    url: str = Field(default="", max_length=300)
    timeout: int = Field(default=45, ge=1, le=90)
    retention_confirmed: bool = False
    usage_note: str = Field(default="", max_length=1000)


class DeepSeekKeyRequest(BaseModel):
    key: SecretStr | None


class DeepSeekTestRequest(BaseModel):
    model: Literal["deepseek-flash", "deepseek-v4-pro"] = "deepseek-flash"


@router.put("/settings/deepseek-key")
def set_deepseek_key(request: Request, user: User, body: DeepSeekKeyRequest):
    require_role(user, "admin")
    value = body.key.get_secret_value().strip() if body.key is not None else None
    if value is not None and (not 12 <= len(value) <= 512 or any(char.isspace() for char in value)):
        raise Problem(422, "INVALID_DEEPSEEK_KEY", "密钥格式不正确，请重新粘贴完整 Key。")
    settings = request.app.state.settings
    save_deepseek_key(settings, value)
    return {"key_configured": bool(deployment_key("deepseek", settings))}


@router.post("/settings/deepseek-test")
def test_deepseek(request: Request, user: User, body: DeepSeekTestRequest):
    require_role(user, "admin")
    key = deployment_key("deepseek", request.app.state.settings)
    if not key:
        raise Problem(422, "DEEPSEEK_KEY_MISSING", "请先保存 DeepSeek Key。")
    try:
        response, _ = request_completion(
            {"provider": "deepseek", "url": DEEPSEEK_URL, "model": body.model, "timeout": 20}, key,
            [{"role": "user", "content": 'Synthetic API check. Return JSON {"ok":true}.'}], 80,
        )
        if json.loads(response).get("ok") is not True:
            raise ValueError
    except Problem as exc:
        if exc.code == "PROVIDER_AUTH":
            raise Problem(422, exc.code, "DeepSeek 拒绝了当前 Key，请核对密钥。") from None
        if exc.code == "PROVIDER_BALANCE":
            raise Problem(422, exc.code, "DeepSeek 账户余额不足。") from None
        raise
    except (http.client.HTTPException, OSError, ValueError, KeyError, TypeError, AttributeError):
        raise Problem(503, "DEEPSEEK_TEST_FAILED", "DeepSeek 连通性或 JSON 响应校验未通过。") from None
    return {"message": "DeepSeek 合成请求成功。此次自检未提交 StudyChat 数据。", "model": body.model}


class ThresholdRequest(BaseModel):
    process_threshold: float = Field(ge=0, le=100, allow_inf_nan=False)
    outcome_threshold: float = Field(ge=0, le=100, allow_inf_nan=False)


class RubricItem(BaseModel):
    item_id: str = Field(pattern=r"^[a-z0-9_-]{1,40}$")
    title: str = Field(min_length=1, max_length=100)
    required: bool
    max_score: int = Field(ge=1, le=100)


class TaskDraft(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    instructions: str = Field(min_length=1, max_length=12000)
    minutes: int = Field(ge=1, le=400)
    deliverables: list[str] = Field(min_length=1, max_length=15)
    rubric: list[RubricItem] = Field(min_length=1, max_length=12)
    public_tests: list[dict[str, str]] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def check_rubric(self):
        if (sum(item.max_score for item in self.rubric) != 100 or
            len({item.item_id for item in self.rubric}) != len(self.rubric) or
            not any(item.required for item in self.rubric)):
            raise ValueError("成果项目必须唯一，总权重为 100，且至少有一项必填")
        return self


class DeleteConfirm(BaseModel):
    expected_preview: str


class Reset(BaseModel):
    confirmation: Literal["RESET DEMO"]


def active_body(db):
    version = active_config(db)
    return version, json.loads(db.execute("SELECT settings FROM evaluation_configs WHERE id=?", (version,)).fetchone()[0])


def put_version(db, body):
    key = digest(dump(body))
    if db.execute("SELECT 1 FROM evaluation_configs WHERE id=?", (key,)).fetchone():
        current, _ = active_body(db)
        if key != current:
            body = {**body, "revision": uid()}
            key = digest(dump(body))
    db.execute("INSERT OR IGNORE INTO evaluation_configs VALUES (?,?,?)", (key, dump(body), now()))
    return key


@router.get("/settings/llm")
def get_llm(request: Request, user: User):
    require_role(user, "admin")
    with database(request.app.state.settings) as db:
        profile = provider_config(db)
        return {**profile, "key_configured": bool(deployment_key(profile["provider"], request.app.state.settings)),
                "deepseek_key_configured": bool(deployment_key("deepseek", request.app.state.settings))}


@router.put("/settings/llm")
def put_llm(request: Request, user: User, body: ProviderRequest):
    require_role(user, "admin")
    if body.provider == "deepseek":
        if body.model not in DEEPSEEK_MODELS:
            raise Problem(422, "DEEPSEEK_MODEL", "请选择支持的 DeepSeek 模型。")
        body.url = DEEPSEEK_URL
    if body.url:
        validate_url(body.url)
    if body.enabled and (not body.retention_confirmed or not body.usage_note.strip() or not body.model.strip()
                         or not body.url or not deployment_key(body.provider, request.app.state.settings)):
        raise Problem(422, "PROVIDER_OPT_IN", "启用前需填写模型、地址、用途及保留策略说明，并从部署环境配置密钥。")
    with database(request.app.state.settings, write=True) as db:
        profile = body.model_dump()
        db.execute("INSERT INTO provider_settings VALUES ('active',?) ON CONFLICT(id) DO UPDATE SET body=excluded.body",
                   (dump(profile),))
        _, settings = active_body(db)
        settings["external_provider"] = profile
        version = put_version(db, settings)
    return {**profile, "config_version": version,
            "key_configured": bool(deployment_key(body.provider, request.app.state.settings))}


@router.get("/settings/evaluation")
def get_evaluation(request: Request, user: User):
    require_role(user, "admin")
    with database(request.app.state.settings, write=True) as db:
        version, config = active_body(db)
    return {"id": version, "bars": BARS, **{key: value for key, value in config.items() if key != "external_provider"}}


@router.put("/settings/evaluation")
def put_evaluation(request: Request, user: User, body: ThresholdRequest):
    require_role(user, "admin")
    with database(request.app.state.settings, write=True) as db:
        _, config = active_body(db)
        data = body.model_dump()
        config.update(data, threshold_version="threshold-" + digest(dump(data))[:12])
        version = put_version(db, config)
        return {"id": version, "bars": BARS, **{key: value for key, value in config.items() if key != "external_provider"}}


@router.get("/settings/versions")
def settings_versions(request: Request, user: User):
    require_role(user, "admin")
    with database(request.app.state.settings) as db:
        items = []
        for row in db.execute("SELECT id,settings,created_at FROM evaluation_configs ORDER BY rowid DESC LIMIT 100"):
            settings = json.loads(row["settings"])
            items.append({"id": row["id"], "created_at": row["created_at"],
                          **{key: settings[key] for key in ("rubric_version", "threshold_version", "prompt_version", "ruleset_version")}})
        return {"items": items}


@router.post("/settings/provider-check")
def provider_check(request: Request, user: User):
    require_role(user, "admin")
    with database(request.app.state.settings) as db:
        profile = provider_config(db)
        db.execute("SELECT 1").fetchone()
    message = "未启用外部模型，Demo 和规则提供器可用。"
    state = "disabled"
    if profile["enabled"]:
        validate_url(profile["url"], dns=True)
        configured = bool(deployment_key(profile["provider"], request.app.state.settings))
        state = "configured" if configured else "missing_key"
        message = "模型地址解析与部署密钥配置检查通过。" if configured else "部署密钥缺失。"
    return {"database": "ready", "execution": "fixture_simulation", "local": DemoProvider().health_check(),
            "external": state, "message": message}


def validate_task_owner(db, user, task_id):
    task = row_or_404(db.execute("SELECT * FROM task_definitions WHERE id=?", (task_id,)).fetchone())
    if user["role"] != "admin" and task["owner_user_id"] != user["id"]:
        raise Problem(403, "TASK_FORBIDDEN", "只能编辑自己负责的任务草稿。")
    if task["status"] != "draft":
        raise Problem(409, "TASK_IMMUTABLE", "已发布的任务版本不可修改。")
    return task


def draft_values(body):
    return (body.title.strip(), body.instructions.strip(), dump([item.model_dump() for item in body.rubric]),
            dump(body.public_tests), dump({"minutes": body.minutes, "deliverables": body.deliverables}))


@router.post("/task-definitions", status_code=201)
def new_task(request: Request, user: User, body: TaskDraft):
    require_role(user, "teacher", "admin")
    with database(request.app.state.settings, write=True) as db:
        task_id = uid()
        course = db.execute("SELECT id FROM courses WHERE owner_user_id=? ORDER BY id LIMIT 1", (user["id"],)).fetchone()
        db.execute("INSERT INTO task_definitions VALUES (?,?,?,?,?,?,?,?,?,?,NULL)",
                   (task_id, "draft", *draft_values(body), "draft", course["id"] if course else None, user["id"]))
        return public_task(db.execute("SELECT * FROM task_definitions WHERE id=?", (task_id,)).fetchone())


@router.put("/task-definitions/{task_id}")
def update_task(request: Request, user: User, task_id: str, body: TaskDraft):
    require_role(user, "teacher", "admin")
    with database(request.app.state.settings, write=True) as db:
        validate_task_owner(db, user, task_id)
        db.execute("UPDATE task_definitions SET title=?,instructions=?,rubric=?,tests=?,metadata=? WHERE id=?",
                   (*draft_values(body), task_id))
        return public_task(db.execute("SELECT * FROM task_definitions WHERE id=?", (task_id,)).fetchone())


@router.post("/task-definitions/{task_id}/publish")
def publish_task(request: Request, user: User, task_id: str):
    require_role(user, "admin")
    with database(request.app.state.settings, write=True) as db:
        task = validate_task_owner(db, user, task_id)
        version = digest(dump([task[key] for key in ("title", "instructions", "rubric", "tests", "metadata")]))[:20]
        db.execute("UPDATE task_definitions SET version=?,status='published' WHERE id=?", (version, task_id))
        return public_task(db.execute("SELECT * FROM task_definitions WHERE id=?", (task_id,)).fetchone())


def impact(db, source):
    sid = source["id"]
    counts = {table: db.execute(query, (sid,)).fetchone()[0] for table, query in {
        "sessions": "SELECT COUNT(*) FROM sessions WHERE source_id=?",
        "events": "SELECT COUNT(*) FROM events e JOIN sessions s ON s.id=e.session_id WHERE s.source_id=?",
        "reports": "SELECT COUNT(*) FROM report_versions r JOIN sessions s ON s.id=r.session_id WHERE s.source_id=?",
        "exports": "SELECT COUNT(*) FROM exports WHERE source_id=?",
    }.items()}
    return {"counts": counts, "preview_token": digest(dump([sid, counts]))}


def delete_source(db, source, user):
    report = impact(db, source)
    db.execute("DELETE FROM data_sources WHERE id=?", (source["id"],))
    db.execute("""DELETE FROM jobs WHERE kind='studychat_preview' AND json_extract(result,'$.batch_id') IN (
                    SELECT id FROM import_batches WHERE owner_user_id=? AND json_extract(metadata,'$.source_identity')=?
                    AND json_extract(metadata,'$.source_version')=?)""",
               (source["owner_user_id"], source["source_identity"], source["source_version"]))
    db.execute("""DELETE FROM import_batches WHERE owner_user_id=? AND json_extract(metadata,'$.source_identity')=?
                  AND json_extract(metadata,'$.source_version')=?""",
               (source["owner_user_id"], source["source_identity"], source["source_version"]))
    db.execute("""DELETE FROM task_definitions WHERE status='source'
                  AND NOT EXISTS(SELECT 1 FROM sessions WHERE task_id=task_definitions.id)""")
    db.execute("INSERT INTO deletion_audit VALUES (?,?,?,?,?)",
               (uid(), user["id"], "data_source", dump(report["counts"]), now()))
    return report["counts"]


@router.get("/datasets/{source_id}/deletion-preview")
def deletion_preview(request: Request, user: User, source_id: str):
    with database(request.app.state.settings) as db:
        source = get_source(db, user, source_id, "manage")
        if user["role"] != "admin" and (not source["demo"] or source["owner_user_id"] != user["id"]):
            raise Problem(403, "DELETE_FORBIDDEN", "教师只能删除自己负责的演示数据。")
        return impact(db, source)


@router.delete("/datasets/{source_id}")
def drop_dataset(request: Request, user: User, source_id: str, body: DeleteConfirm):
    with database(request.app.state.settings, write=True) as db:
        source = get_source(db, user, source_id, "manage")
        if user["role"] != "admin" and (not source["demo"] or source["owner_user_id"] != user["id"]):
            raise Problem(403, "DELETE_FORBIDDEN", "教师只能删除自己负责的演示数据。")
        if body.expected_preview != impact(db, source)["preview_token"]:
            raise Problem(409, "DELETE_PREVIEW", "影响范围已有变化，请重新确认。")
        return {"deleted": delete_source(db, source, user)}


@router.post("/settings/reset-demo")
def reset_demo(request: Request, user: User, body: Reset):
    require_role(user, "admin")
    with database(request.app.state.settings, write=True) as db:
        sources = db.execute("SELECT * FROM data_sources WHERE demo=1").fetchall()
        for source in sources:
            delete_source(db, source, user)
        return {"deleted_data_sources": len(sources)}
