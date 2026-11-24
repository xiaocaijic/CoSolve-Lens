import json
from collections import Counter
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .core import Problem, digest, dump, now, uid
from .fixtures import checked_fixture
from .outcomes import diagnosis_for
from .rubrics import (DIMENSIONS, FIVE_MISSING, FIVE_NO_OPPORTUNITY, FIVE_SETTINGS,
                      LEGACY_DIMENSIONS, DimensionName, is_five_dimension, level_bounds, rubric_dimensions)

ITEMS = [
    {"item_id": "function", "title": "核心功能和测试", "required": True, "max_score": 50},
    {"item_id": "constraints", "title": "任务限制满足情况", "required": True, "max_score": 20},
    {"item_id": "quality", "title": "交付质量", "required": True, "max_score": 15},
    {"item_id": "explanation", "title": "做法和局限说明", "required": True, "max_score": 15},
]
METHODS = ("rules", "direct_llm", "hybrid", "demo_conflict")
DEFAULT_CONFIG = {
    **FIVE_SETTINGS, "threshold_version": "threshold-1", "process_threshold": 67, "outcome_threshold": 75,
}
REASONS = {
    "NO_ORGANIZATION_OPPORTUNITY": "任务只需机械提交已有文件，无需组织解题步骤。",
    "NO_VERIFIABLE_ARTIFACT": "固定任务本身没有可核对或可测试的交付物。",
    "NO_DIAGNOSTIC_EVENT": "完整记录中没有观察到需要诊断的异常。",
    "NO_AI_USE": "完整记录中没有观察到用于解题的 AI 建议。",
    "RULES_CANNOT_JUDGE_SEMANTICS": "客观记录不足以让规则判断该维度的语义表现。",
    "MISSING_TASK": "缺少可核对的题目说明。",
    "MISSING_RUN_LOG": "缺少最终版本的运行或测试记录。",
    "SYNTHETIC_EXECUTION": "静态演示运行只能用于展示。",
    "UNVERIFIED_EXECUTION": "运行的来源或真实性未验证。",
    "UNALIGNED_ARTIFACT_VERSION": "运行或测试记录无法对应提交版本。",
    "CONFLICTING_RECORDS": "候选判断与可核验的事实冲突，等待教师复核。",
    "MISSING_AI_LOG": "对话或采用后的代码版本缺失。",
    "MISSING_DIAGNOSTIC_LOG": "缺少错误后的修改或验证记录。",
    **{code: f"现有记录不足以判断{DIMENSIONS[name]}表现。" for name, code in FIVE_MISSING.items()},
    **{code: f"任务与完整记录表明未出现{DIMENSIONS[name]}的评价机会。"
       for name, code in FIVE_NO_OPPORTUNITY.items()},
}
NOT_APPLICABLE_REASONS = {
    "organization": "NO_ORGANIZATION_OPPORTUNITY", "verification": "NO_VERIFIABLE_ARTIFACT",
    "diagnosis": "NO_DIAGNOSTIC_EVENT", "ai_integration": "NO_AI_USE",
    **FIVE_NO_OPPORTUNITY,
}


class Dimension(BaseModel):
    dimension: DimensionName
    state: Literal["scored", "not_applicable", "insufficient_evidence"]
    level: int | None = Field(default=None, ge=0, le=5, strict=True)
    reason: str | None = None
    rationale: str = Field(min_length=1, max_length=2000)
    event_ids: list[str] = Field(default_factory=list, max_length=30)
    confidence: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def valid_state(self):
        if self.state == "scored":
            if self.level is None or self.reason or not self.event_ids:
                raise ValueError("等级必须包含证据")
        elif self.level is not None or self.reason not in REASONS:
            raise ValueError("特殊状态必须包含有效原因码")
        elif self.state == "not_applicable" and self.reason != NOT_APPLICABLE_REASONS[self.dimension]:
            raise ValueError("不适用原因必须表明该维度没有评价机会")
        elif self.state == "insufficient_evidence" and self.reason in NOT_APPLICABLE_REASONS.values():
            raise ValueError("证据不足不能使用无评价机会的原因")
        return self


def initial_config(db, config=None):
    value = DEFAULT_CONFIG if config is None else config
    version = digest(dump(value))
    db.execute("INSERT OR IGNORE INTO evaluation_configs VALUES (?,?,?)", (version, dump(value), now()))
    return version


def active_config(db):
    row = db.execute("SELECT id,settings FROM evaluation_configs ORDER BY rowid DESC LIMIT 1").fetchone()
    if not row:
        return initial_config(db)
    config = json.loads(row["settings"])
    if any(config.get(name) != value for name, value in FIVE_SETTINGS.items()):
        return initial_config(db, config | FIVE_SETTINGS)
    return row["id"]


def score(dimension, level, rationale, events):
    return Dimension(dimension=dimension, state="scored", level=level, rationale=rationale,
                     event_ids=[event["id"] for event in events]).model_dump()


def special(dimension, reason, state="insufficient_evidence"):
    return Dimension(dimension=dimension, state=state, reason=reason, rationale=REASONS[reason]).model_dump()


def event_payloads(db, session_id, cutoff):
    return [{**dict(event), "payload": json.loads(event["payload"])} for event in db.execute(
        "SELECT * FROM events WHERE session_id=? AND sequence_no<=? ORDER BY sequence_no", (session_id, cutoff)
    )]


def collect_facts(events):
    submission = next((event for event in reversed(events) if event["type"] == "submission_created"), None)
    checksum = submission["payload"].get("artifact_checksum") if submission else None
    executions = [event for event in events if event["type"] in ("run_finished", "test_finished") and
                  (not submission or event["sequence_no"] < submission["sequence_no"])]
    verified = [event for event in executions if event["payload"].get("verified") and
                not event["payload"].get("synthetic") and event["payload"].get("execution_mode") != "external_unknown"]
    submission_payload = submission["payload"] if submission else {}
    final = [event for event in verified if checksum and event["payload"].get("artifact_checksum") == checksum
             and event["payload"]["artifact_id"] in submission_payload.get("artifact_refs", [])
             and event["payload"]["artifact_version"] == submission_payload.get("artifact_version")]
    failed = [event for event in verified if event["payload"].get("exit_code", 0) != 0
              or event["payload"].get("failed", 0) > 0]
    passed = [event for event in final if event["type"] == "test_finished"
              and event["payload"]["passed"] > 0 and event["payload"]["failed"] == 0]
    # The same test may be repeated. Only contradictory outcomes for the
    # same test case on the same submitted snapshot indicate a conflict.
    observations = {}
    final_conflicts = set()
    for event in final:
        payload = event["payload"]
        if event["type"] == "run_finished":
            codes = [("run_exit", payload["exit_code"] == 0)]
        else:
            codes = [("test:" + str(case["id"]), case["passed"]) for case in payload["cases"]
                     if isinstance(case.get("id"), str) and type(case.get("passed")) is bool]
        for key, success in codes:
            old = observations.get(key)
            if old and old[0] != success:
                final_conflicts.update((old[1], event["id"]))
            observations[key] = success, event["id"]
    errors = Counter(event["payload"].get("stderr", "").strip() for event in failed)
    snapshots = [event for event in events if event["type"] == "code_snapshot"]
    ai = [event for event in events if event["type"] == "chat_ai_message"]
    return {
        "submission": submission, "checksum": checksum, "executions": executions, "verified": verified,
        "final": final, "failed": failed, "passed": passed, "snapshots": snapshots, "ai": ai,
        "final_conflicts": [event["id"] for event in final if event["id"] in final_conflicts],
        "repeat_error": [digest(text) for text, count in errors.items() if text and count > 1],
        "snapshot_after_failure": any(s["sequence_no"] > f["sequence_no"] and
                                      s["payload"]["version"] != f["payload"]["artifact_version"]
                                      for s in snapshots for f in failed),
        "snapshot_after_ai": any(s["sequence_no"] > a["sequence_no"] for s in snapshots for a in ai),
    }


def verification_result(facts, complete_log):
    name = "verification"
    submission, final, executions = facts["submission"], facts["final"], facts["executions"]
    if facts["final_conflicts"]:
        return special(name, "CONFLICTING_RECORDS")
    if final:
        boundary = [event for event in facts["passed"]
                    if any(case.get("category") == "boundary" and case.get("passed")
                           for case in event["payload"]["cases"])]
        if boundary and complete_log:
            runs = [event for event in final if event["type"] == "run_finished" and event["payload"]["exit_code"] == 0]
            return score(name, 3, "提交快照关联主流程和边界测试的通过记录。", [*runs[-1:], boundary[-1], submission])
        main = [event for event in final if event["type"] == "test_finished" and event["payload"]["passed"] > 0]
        if main:
            return score(name, 2, "提交快照有已通过的测试，部分要求仍缺少验证。", [main[-1], submission])
        return score(name, 1, "提交快照有执行记录，缺少关键结果通过的测试。", [final[-1], submission])
    if any(event["payload"].get("synthetic") for event in executions):
        return special(name, "SYNTHETIC_EXECUTION")
    if any(event["payload"].get("verification_error") == "UNALIGNED_ARTIFACT_VERSION" for event in executions):
        return special(name, "UNALIGNED_ARTIFACT_VERSION")
    if any(not event["payload"].get("verified") for event in executions):
        return special(name, "UNVERIFIED_EXECUTION")
    if executions:
        return special(name, "UNALIGNED_ARTIFACT_VERSION")
    if complete_log and submission and facts["snapshots"]:
        return score(name, 0, "完整记录中最终版本未经运行或测试即提交。", [facts["snapshots"][-1], submission])
    return special(name, "MISSING_RUN_LOG")


def rules(task, facts, complete_log):
    organization = special("organization", "RULES_CANNOT_JUDGE_SEMANTICS" if task["instructions"] else "MISSING_TASK")
    diagnosis = special("diagnosis", "RULES_CANNOT_JUDGE_SEMANTICS" if facts["snapshot_after_failure"]
                        else "MISSING_DIAGNOSTIC_LOG")
    if complete_log and not facts["failed"]:
        diagnosis = special("diagnosis", "NO_DIAGNOSTIC_EVENT", "not_applicable")
    ai = special("ai_integration", "RULES_CANNOT_JUDGE_SEMANTICS" if facts["snapshot_after_ai"] else "MISSING_AI_LOG")
    if complete_log and not facts["ai"]:
        ai = special("ai_integration", "NO_AI_USE", "not_applicable")
    return [organization, verification_result(facts, complete_log), diagnosis, ai]


DEMO_RATIONALES = {
    "organization": ("持续误解任务目标。", "主要以笼统请求推进，分步证据有限。",
                     "对主要目标清楚，部分组织细节缺少记录。", "明确目标和约束，并列出操作步骤。"),
    "verification": ("最终快照未运行即提交。", "记录显示运行和报错，关键结果尚未核对。",
                     "核对了主要结果，边界要求仍未完全通过。", "提交版本通过主要和边界验证。"),
    "diagnosis": ("相同错误重复出现，修改未针对出错字段。", "修改只部分解决异常。",
                  "定位异常并修改后重测，部分要求尚未满足。", "根据失败定位原因，有针对性地修改并重测。"),
    "ai_integration": ("采用与核心要求冲突的建议。", "主要照搬建议，缺少关键适配证据。",
                       "结合建议完成适配，后续验证尚未覆盖所有要求。", "调整了建议的做法并核对修改结果。"),
}


class DemoProvider:
    name = "demo"
    model = "scripted-fixtures-v1"

    def evaluate_session(self, fixture, by_key, *, conflict=False):
        expected = checked_fixture(f"{fixture}_demo_expected.json")
        dimensions = []
        for item in expected["dimensions"]:
            name, level = item["dimension"], item["level"]
            if level is None:
                dimensions.append(special(name, item["reason"], item["state"]))
            else:
                try:
                    refs = [by_key[key] for key in item["event_keys"]]
                except KeyError:
                    raise Problem(422, "INVALID_EVIDENCE", "演示候选引用了不存在的事件。") from None
                dimensions.append(score(name, level, DEMO_RATIONALES[name][level], refs) |
                                  {"confidence": 0.65 if level == 1 else 0.85})
        claim = None
        if conflict:
            claim = {"dimension": "verification", "claim": "final_tests_failed", "level": 1,
                     "text": "候选判断：最终版本未通过测试。", "event_ids": [by_key["t5a"]["id"]]}
            dimensions[1] = score("verification", 1, claim["text"], [by_key["t5a"]])
        return dimensions, claim, expected["outcome_items"]

    def chat(self, messages, task_context):
        text = messages[-1]["text"]
        if any(word in text for word in ("那", "继续", "刚才", "具体", "下一步")):
            text = text + " " + " ".join(message["text"] for message in messages[-4:-1] if message["role"] == "user")
        if "退款" in text:
            return "先用订单状态标记退款并保留记录。构造一条正常销售和一条退款，核对月净额是否等于两者的有符号金额之和。"
        if "日期" in text:
            return "先用固定格式解析，再把无效日期记录单独计数。可以用一条有效日期和一条不存在的日期验证分支。"
        return "先列出输入、预期输出和数据约束。用一个小样本分别检查缺失值、重复订单、异常日期和退款。"

    def health_check(self):
        return {"status": "available", "provider": self.name, "model": self.model, "simulated": True}


def make_outcome(rubric, facts, fixture_items=None):
    submission = facts["submission"]
    items = []
    for index, item in enumerate(rubric):
        value, refs, method = None, [], "rule"
        reason = "缺少可核验的分项评分证据。"
        if fixture_items is not None:
            value, refs = fixture_items[index], [submission["id"]]
            reason = "版本化演示案例的固定成果分项。"
        elif item["item_id"] == "function":
            tests = [event for event in facts["final"] if event["type"] == "test_finished"]
            reason = "缺少可核验的最终版本成果测试。"
            if tests and not facts["final_conflicts"] and tests[-1]["payload"].get("outcome_suite"):
                test = tests[-1]
                passed, failed = test["payload"]["passed"], test["payload"]["failed"]
                if passed + failed:
                    value = rounded(Decimal(passed) * item["max_score"] / (passed + failed))
                    refs, method, reason = [test["id"]], "test", "根据最终交付物的成果测试比例计算。"
        items.append({**item, "score": value, "evidence_refs": refs, "method": method, "reason": reason,
                      "state": "scored" if value is not None else "missing_evidence"})
    complete = bool(items) and all(not item["required"] or item["state"] == "scored" for item in items)
    total = rounded(sum(Decimal(str(item["score"])) for item in items if item["score"] is not None)) if complete else None
    return {"score": total, "complete": complete, "items": items, "reason": None if complete else "MISSING_OUTCOME_EVIDENCE"}


def rounded(value):
    return float(Decimal(value).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def five_point_score(percent):
    return (float((Decimal(str(percent)) / 20).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
            if percent is not None else None)


def recompute(report, config):
    dims = report["dimensions"]
    scored = [dim for dim in dims if dim["state"] == "scored"]
    five = is_five_dimension(config["rubric_version"])
    allowed = len(scored) >= 3 if five else (
        len(scored) >= 2 and any(dim["dimension"] == "verification" for dim in scored)
        and sum(dim["state"] == "insufficient_evidence" for dim in dims) <= 1)
    maximum = level_bounds(config["rubric_version"])[1]
    raw_process = Decimal(sum(dim["level"] for dim in scored)) * 100 / (len(scored) * maximum) if allowed else None
    process = rounded(raw_process) if allowed else None
    outcome = report.get("display_outcome_score", report["outcome"]["score"])
    display_basis = raw_process if maximum == 5 else process
    report.update(process_score=process, process_score_five=five_point_score(display_basis),
                  process_reason=None if allowed else "PROCESS_EVIDENCE_GATE",
                  diagnosis=diagnosis_for(process, outcome, config))
    if five:
        report["process_coverage"] = {"scored_count": len(scored), "total": len(dims), "minimum": 3}
    report["status"] = "partial" if process is None or outcome is None else "completed"


def short_excerpt(event):
    payload = event["payload"]
    value = next((payload.get(key) for key in ("text", "content", "stderr", "stdout", "statement")
                  if payload.get(key)), None)
    if value is None and event["type"] == "test_finished":
        value = f"通过 {payload.get('passed', '未知')} 项，失败 {payload.get('failed', '未知')} 项"
    return str(value or event["type"])[:240]


def annotate(dimension, events_by_id, source, *, kind=None):
    kind = kind or ("positive" if dimension["level"] in (2, 3) else "issue")
    return {"dimension": dimension["dimension"], "type": kind, "text": dimension["rationale"],
            "segment": "任务过程", "source": source,
            "confidence": dimension.get("confidence") if dimension.get("confidence") is not None else
            (1 if source == "rule" else 0.5 if source == "llm" else 1),
            "requires_review": kind in {"rule_llm_conflict", "record_conflict"},
            "evidence_refs": [{"event_id": item, "excerpt": short_excerpt(events_by_id[item])}
                              for item in dict.fromkeys(dimension["event_ids"])]}


def limit_studychat(dimensions, events, task):
    by_id = {event["id"]: event for event in events}
    result = list(dimensions)
    result[1] = special("verification", "MISSING_RUN_LOG")
    for index, reason in ((0, "MISSING_TASK"), (2, "MISSING_DIAGNOSTIC_LOG"), (3, "MISSING_AI_LOG")):
        item = result[index]
        refs = [by_id[event_id] for event_id in item["event_ids"]]
        users = [event["sequence_no"] for event in refs if event["type"] == "chat_user_message"]
        assistants = [event["sequence_no"] for event in refs if event["type"] == "chat_ai_message"]
        if index == 0 and (not task["instructions"] or (item["state"] == "scored" and not users)):
            result[index] = special(item["dimension"], reason if not task["instructions"] else "RULES_CANNOT_JUDGE_SEMANTICS")
        elif index == 2 and (item["state"] == "not_applicable" or (item["state"] == "scored" and not users)):
            result[index] = special(item["dimension"], reason)
        elif index == 3 and (item["state"] == "not_applicable" or (item["state"] == "scored" and
                (not users or not assistants or max(users) <= min(assistants)))):
            result[index] = special(item["dimension"], reason)
    return result


def evaluate(run, session, task, events, config, external_allowed=False, studychat=False, settings=None, *, pilot=None, progress=None):
    if is_five_dimension(config["rubric_version"]):
        from .five_evaluation import evaluate_five

        return evaluate_five(run, session, task, events, config, external_allowed, studychat, settings, pilot, progress)
    return evaluate_legacy(run, session, task, events, config, external_allowed, studychat, settings)


def evaluate_legacy(run, session, task, events, config, external_allowed, studychat, settings):
    metadata = json.loads(session["metadata"])
    fixture = metadata.get("fixture_id")
    by_key = {event["event_key"]: event for event in events}
    by_id = {event["id"]: event for event in events}
    facts = collect_facts(events)
    baseline = rules(task, facts, bool(fixture))
    dimensions, candidate, conflicts, warnings = baseline, None, [], []
    provider, model, claim = "rules", config["ruleset_version"], None
    audit = None
    degraded = run["method"] != "rules"
    fixture_items = checked_fixture(f"{fixture}_demo_expected.json")["outcome_items"] if fixture else None
    if degraded and fixture and (metadata.get("golden_only") is not False):
        demo_provider = DemoProvider()
        candidate, claim, fixture_items = demo_provider.evaluate_session(
            fixture, by_key, conflict=run["method"] == "demo_conflict"
        )
        dimensions = list(candidate)
        provider, model = demo_provider.name, demo_provider.model
        warnings.append("未配置外部语义模型，使用固定 Demo 候选结果。")
    elif degraded and external_allowed and config.get("external_provider", {}).get("enabled"):
        from .providers import external_evaluate

        passed = bool(facts["passed"])
        latest = facts["final"][-1] if facts["final"] else None
        aligned_failed = bool(latest and latest in facts["failed"])
        output, audit = external_evaluate(config, task, events, run["method"], {
            "final_tests_passed": passed, "final_execution_failed": aligned_failed,
            "final_execution_event_ids": [event["id"] for event in facts["final"]],
        }, studychat=studychat, settings=settings)
        if output is None:
            warnings.append("外部语义评价未完成，已降级为规则评价。")
        else:
            provider, model = audit["provider"], audit["model"]
            lookup = {item.dimension: item.model_dump() for item in output.dimensions}
            candidate = [lookup[name] for name in LEGACY_DIMENSIONS]
            dimensions = list(candidate)
            degraded = False
            report_claim = output.final_verification
            matching = {event["id"] for event in facts["final"]}
            model_verification = lookup["verification"]
            referenced_final = matching & set(model_verification["event_ids"])
            if ((passed and report_claim in {"failed", "not_run"}) or
                (aligned_failed and report_claim in {"passed", "not_run"}) or
                (latest and report_claim == "not_run")):
                claim = {"dimension": "verification", "claim": report_claim, "level": lookup["verification"]["level"],
                         "text": lookup["verification"]["rationale"], "event_ids": output.final_verification_event_ids}
            elif (not facts["final"] or not referenced_final or facts["final_conflicts"] or
                  (passed and model_verification["level"] == 0) or
                  (aligned_failed and not passed and model_verification["level"] == 3)):
                dimensions[1] = baseline[1]
            if studychat:
                dimensions = limit_studychat(dimensions, events, task)
                warnings.append("StudyChat 主要提供历史对话。单维等级只覆盖观察到的行为，缺少可核验运行时不计算过程总分。")
    elif degraded:
        warnings.append("未配置或未授权当前资料的语义模型，已降级为规则评价。")
    if fixture:
        warnings.append("模拟证据，仅用于工程演示")
    annotations = [annotate(item, by_id, "llm" if candidate and item in candidate else "rule")
                   for item in dimensions if item["event_ids"]]
    if facts["final_conflicts"]:
        dimensions[1] = special("verification", "CONFLICTING_RECORDS")
        warnings.append("同一提交快照存在相互矛盾的执行结果，请教师核对。")
        annotations = [item for item in annotations if item["dimension"] != "verification"]
        annotations.append(annotate({"dimension": "verification", "level": None,
                                     "rationale": "同一提交快照存在相互矛盾的执行记录。",
                                     "event_ids": facts["final_conflicts"]}, by_id, "rule", kind="record_conflict"))
    if claim and facts["final"]:
        decisive = facts["passed"][-1] if facts["passed"] else facts["final"][-1]
        rule_ids = [decisive["id"], facts["submission"]["id"]]
        conflicts.append({"state": "pending_review", "dimension": "verification", "candidate": claim,
                          "rule_event_ids": rule_ids, "rule_result": baseline[1]})
        dimensions[1] = special("verification", "CONFLICTING_RECORDS")
        annotations = [item for item in annotations if item["dimension"] != "verification"]
        annotations.append(annotate({"dimension": "verification", "level": None,
                                     "rationale": REASONS["CONFLICTING_RECORDS"],
                                     "event_ids": [*claim["event_ids"], *rule_ids]}, by_id, "rule",
                                    kind="rule_llm_conflict"))
    verified_summary = {
        "final_checksum": facts["checksum"], "final_execution_event_ids": [event["id"] for event in facts["final"]],
        "repeat_error_signatures": facts["repeat_error"], "snapshot_after_failure": facts["snapshot_after_failure"],
        "snapshot_after_ai": facts["snapshot_after_ai"], "conflicting_execution_event_ids": facts["final_conflicts"],
    }
    task_rubric = json.loads(task["rubric"]) or ITEMS
    report = {
        "run_id": run["id"], "session_id": session["id"], "source_type": session["source_type"],
        "method": run["method"], "purpose": run["purpose"], "degraded": degraded,
        "dimensions": dimensions, "outcome": make_outcome(task_rubric, facts, fixture_items),
        "annotations": annotations, "warnings": warnings, "demo_only": bool(fixture) or run["purpose"] == "demo",
        "teaching_suggestions": [] if conflicts or facts["final_conflicts"] else suggestions(dimensions),
        "conflicts": conflicts, "raw_rule_facts": verified_summary, "raw_rule_dimensions": baseline,
        "raw_llm_candidate": {"dimensions": candidate, "claim": claim} if candidate else None,
        "provider_call": audit,
        "task_version": task["version"], "provider": provider, "model": model,
        "model_params_hash": digest(dump({"model": model, "temperature": audit["temperature"] if audit and not degraded else 0,
                                          "max_tokens": audit["token_limit"] if audit and not degraded else 0})),
        "config_version": run["config_version"],
        **{key: config[key] for key in ("rubric_version", "threshold_version", "prompt_version", "ruleset_version")},
    }
    recompute(report, config)
    return report


def suggestions(dimensions):
    result = []
    lookup = {item["dimension"]: item for item in dimensions}
    verification = lookup["verification"]
    if verification["state"] != "scored" or verification["level"] < 3:
        result.append("保存最终版本运行和测试记录，核对主流程与退款、缺失行等边界。")
    if lookup["organization"]["state"] != "scored":
        result.append("保存任务约束、输入输出和分步计划，供后续对照。")
    return result


def require_event_ids(event_ids, events_by_id):
    if not all(event_id in events_by_id for event_id in event_ids):
        raise Problem(422, "INVALID_EVIDENCE", "证据必须属于此报告的会话快照。")


def validate_scored_evidence(dimension, events):
    if dimension["state"] != "scored":
        return
    for event_id in dimension["event_ids"]:
        event = events[event_id]
        if event["type"] in ("run_started", "run_finished", "test_finished"):
            payload = event["payload"]
            if payload.get("synthetic") or not payload.get("verified"):
                raise Problem(422, "NON_SCOREABLE_EXECUTION", "模拟或未核验执行只能作为线索，不能支撑正式等级。")


def store_report(db, run, report, *, parent_id=None, effective=True, release=False, system_correction=False):
    events = {item["id"]: item for item in event_payloads(db, run["session_id"], run["event_cutoff"])}
    if is_five_dimension(report["rubric_version"]) and report["provider"] == "deepseek":
        from .studychat_policy import is_snapshot
        from .studychat_quality import cited_dialogue_ids, session_quality

        session = db.execute("SELECT * FROM sessions WHERE id=?", (run["session_id"],)).fetchone()
        source = db.execute("SELECT adapter,source_identity FROM data_sources WHERE id=?", (session["source_id"],)).fetchone()
        if is_snapshot(source):
            task = db.execute("SELECT instructions FROM task_definitions WHERE id=?", (session["task_id"],)).fetchone()
            quality = session_quality(db, session, task, events.values())
            excluded = set(quality["excluded_event_ids"]) | set(report.get("out_of_task_event_ids", []))
            if quality["eligibility_status"] != "eligible" or excluded & cited_dialogue_ids(report):
                raise Problem(422, "STUDYCHAT_EVIDENCE_SCOPE", "正式评价证据须来自通过质量检查并与本作业相关的对话。")
    names = rubric_dimensions(report["rubric_version"])
    if sorted(dim["dimension"] for dim in report["dimensions"]) != sorted(names):
        raise Problem(422, "REPORT_RUBRIC", "维度必须与报告量规一致。")
    for item in report["dimensions"]:
        Dimension.model_validate(item)
        minimum, maximum = level_bounds(report["rubric_version"])
        if item["level"] is not None and not minimum <= item["level"] <= maximum:
            raise Problem(422, "REPORT_LEVEL", "维度等级超出该报告的量规范围。")
        require_event_ids(item["event_ids"], events)
        validate_scored_evidence(item, events)
    for item in report["outcome"]["items"]:
        require_event_ids(item["evidence_refs"], events)
    for key in ("ai_behaviors", "teaching_recommendations", "teacher_recommendations", "student_recommendations", "transfer_tasks"):
        for item in report.get(key, []):
            if item["dimension"] not in names or not item["event_ids"]:
                raise Problem(422, "REPORT_RUBRIC", "报告建议和行为标签必须引用该量规与证据。")
            require_event_ids(item["event_ids"], events)
    if report.get("teaching_diagnosis"):
        require_event_ids(report["teaching_diagnosis"]["event_ids"], events)
    require_event_ids(report.get("out_of_task_event_ids", []), events)
    for correction in report.get("system_quality_review", {}).get("changes", []):
        require_event_ids(correction["event_ids"], events)
    version = db.execute("SELECT COALESCE(MAX(version),0)+1 FROM report_versions WHERE run_id=?", (run["id"],)).fetchone()[0]
    report_id, created_at = uid(), now()
    kind = "reviewed" if parent_id and not system_correction else "system"
    report.update(id=report_id, version=version, parent_id=parent_id, kind=kind, created_at=created_at)
    report["dimensions"] = [{**item, "annotation_ids": []} for item in report["dimensions"]]
    annotations = []
    for item in report["annotations"]:
        event_ids = [ref["event_id"] for ref in item["evidence_refs"]]
        if not event_ids or item["dimension"] not in names:
            raise Problem(422, "INVALID_EVIDENCE", "批注需要至少一条事件证据。")
        require_event_ids(event_ids, events)
        annotation = {**item, "id": uid(), "evidence_refs": [
            {"event_id": event_id, "excerpt": short_excerpt(events[event_id])} for event_id in dict.fromkeys(event_ids)
        ]}
        annotations.append(annotation)
        for dim in report["dimensions"]:
            if dim["dimension"] == annotation["dimension"]:
                dim["annotation_ids"].append(annotation["id"])
    report["annotations"] = annotations
    db.execute("INSERT INTO report_versions VALUES (?,?,?,?,?,?,?,?,?,?)", (
        report_id, run["id"], run["session_id"], run["method"], run["purpose"], version, parent_id, kind, dump(report), created_at
    ))
    for item in annotations:
        db.execute("INSERT INTO annotations VALUES (?,?,?,?,?,?)", (
            item["id"], report_id, item["dimension"], item["type"], item["text"], item["confidence"]
        ))
        db.executemany("INSERT INTO evidence_links VALUES (?,?,?)", [
            (item["id"], ref["event_id"], ref["excerpt"]) for ref in item["evidence_refs"]
        ])
    db.executemany("INSERT INTO dimension_results VALUES (?,?,?,?,?)", [
        (report_id, dim["dimension"], dim["state"], dim["level"], dim["rationale"]) for dim in report["dimensions"]
    ])
    db.execute("INSERT INTO outcome_results VALUES (?,?,?,?)", (
        report_id, report["outcome"]["score"], dump(report["outcome"]["items"]), report["outcome"]["complete"]
    ))
    diag = report["diagnosis"]
    db.execute("INSERT INTO diagnoses VALUES (?,?,?,?)", (report_id, diag["process_band"], diag["outcome_band"], diag["label"]))
    if effective:
        db.execute(
            "INSERT INTO effective_reports VALUES (?,?,?,?,?) ON CONFLICT(session_id,method,purpose)"
            " DO UPDATE SET report_id=excluded.report_id,released_to_student=excluded.released_to_student",
            (run["session_id"], run["method"], run["purpose"], report_id, release),
        )
    return report
