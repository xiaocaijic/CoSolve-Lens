from .builtin_studychat import IDENTITY
from .core import Problem
from .providers import STUDYCHAT_POLICY_VERSION, deployment_key, provider_config

SCOPE = "仅发送任务说明、必要且脱敏的学生与 AI 对话摘录、内部事件编号。成绩、原始标识及归档作业保留在本机。"


def is_snapshot(source):
    return bool(source and source["adapter"] == "studychat"
                and source["source_identity"] in {IDENTITY, "studychat-v1.1"})


def consent_row(db, source_id):
    return db.execute("""SELECT enabled,scope_version,purpose,created_at FROM studychat_deepseek_consents
                         WHERE source_id=? ORDER BY rowid DESC LIMIT 1""", (source_id,)).fetchone()


def consented(db, source_id):
    row = consent_row(db, source_id)
    return bool(row and row["enabled"] and row["scope_version"] == STUDYCHAT_POLICY_VERSION)


def require_deepseek(db, settings, source_id):
    from .jobs import active_config
    from .rubrics import is_five_dimension

    profile = provider_config(db)
    if (profile["provider"] != "deepseek" or not profile["enabled"] or not profile["retention_confirmed"]
            or not deployment_key("deepseek", settings)):
        raise Problem(409, "DEEPSEEK_SETUP", "请在管理员的系统设置中配置 DeepSeek 密钥并启用模型。")
    if not consented(db, source_id):
        raise Problem(409, "DEEPSEEK_SCOPE_NOT_APPROVED", "请由管理员在此数据源确认 DeepSeek 的字段范围和使用策略。")
    active = db.execute("SELECT json_extract(settings,'$.rubric_version') FROM evaluation_configs WHERE id=?",
                        (active_config(db),)).fetchone()
    if is_five_dimension(active[0]):
        raise Problem(409, "STUDYCHAT_SAMPLE_SCOPE", "五维完整对话评价仅适用于逐条授权的 StudyChat 试跑会话。")
