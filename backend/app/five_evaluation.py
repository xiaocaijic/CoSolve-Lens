import json
import re

from .core import Problem, digest, dump
from .evaluation import (ITEMS, REASONS, DemoProvider, annotate, collect_facts, make_outcome,
                         recompute, score, special, verification_result)
from .five_provider import semantic_evaluation
from .outcomes import outcome_fields, source_grade
from .rubrics import DIMENSIONS, FIVE_MISSING, FIVE_POINT_VERSION


RESULT = "result_evaluation"
TEST_REQUEST = re.compile(
    r"(?i)\b(?:can you|could you|how (?:can|do|would) (?:i|we))\s+(?:please\s+)?"
    r"(?:test|verify|validate|double.check)\b|(?:能否|可以|请|如何|怎么).{0,12}(?:写.{0,6}测试|验证|核对|举.{0,5}反例)"
)


def result_rule(facts, events, fixture):
    old = verification_result(facts, bool(fixture)) | {"dimension": RESULT}
    if old["state"] == "scored" or old["reason"] == "CONFLICTING_RECORDS":
        return old
    checks = [event for event in events if event["type"] == "chat_user_message" and
              TEST_REQUEST.search(event["payload"].get("text", ""))]
    if checks:
        return score(RESULT, 1, "学生主动提出具体的测试或核对请求。规则可确认请求，后续执行与结论需要结合完整对话判断。",
                     checks[:8])
    if old["reason"] in {"SYNTHETIC_EXECUTION", "UNVERIFIED_EXECUTION", "UNALIGNED_ARTIFACT_VERSION"}:
        return old
    return special(RESULT, FIVE_MISSING[RESULT])


def demo_dimensions(fixture, events, conflict):
    by_key = {event["event_key"]: event for event in events}
    legacy, claim, items = DemoProvider().evaluate_session(fixture, by_key, conflict=conflict)
    names = {"organization": "problem_understanding", "verification": RESULT, "diagnosis": "analytical_reasoning"}
    mapped = {names[item["dimension"]]: item | {"dimension": names[item["dimension"]]} for item in legacy
              if item["dimension"] in names and item["state"] == "scored"}
    if fixture == "F1":
        name = "analytical_reasoning"
        mapped[name] = score(name, 1, "学生给出清洗、去重和核对的先后顺序，对这些步骤的因果说明有限。", [by_key["u1"]])
    # The fixed fixtures only encode those three constructs directly. AI-use grades are never copied.
    if claim:
        claim["dimension"] = RESULT
    return [mapped.get(name, special(name, FIVE_MISSING[name])) for name in DIMENSIONS], claim, items


def rule_summary(facts):
    return {"final_checksum": facts["checksum"], "final_execution_event_ids": [e["id"] for e in facts["final"]],
            "repeat_error_signatures": facts["repeat_error"], "snapshot_after_failure": facts["snapshot_after_failure"],
            "snapshot_after_ai": facts["snapshot_after_ai"], "conflicting_execution_event_ids": facts["final_conflicts"]}


def versioned_rule_level(item, five_points):
    level = item.get("level")
    return item | {"level": (1, 2, 3, 5)[level]} if five_points and level is not None else item


def evaluate_five(run, session, task, events, config, external_allowed, studychat, settings, pilot, progress=None):
    quality = pilot.get("quality") if pilot else None
    if studychat and not (quality and quality["eligibility_status"] == "eligible" and external_allowed and run["method"] == "hybrid"):
        raise Problem(409, "STUDYCHAT_MODEL_REQUIRED", "StudyChat 五维报告须先通过质量检查，再使用授权模型。")
    metadata = json.loads(session["metadata"])
    fixture = metadata.get("fixture_id")
    by_id = {event["id"]: event for event in events}
    facts = collect_facts(events)
    five_points = config["rubric_version"] == FIVE_POINT_VERSION
    rule_result = versioned_rule_level(result_rule(facts, events, fixture), five_points)
    baseline = [rule_result if name == RESULT else special(name, FIVE_MISSING[name]) for name in DIMENSIONS]
    if fixture:
        _, _, fixture_items = demo_dimensions(fixture, events, False)
    else:
        fixture_items = None
    dimensions, candidate, claim, audit, model_result = list(baseline), None, None, None, None
    provider, model = "rules", config["ruleset_version"]
    degraded = run["method"] != "rules"
    warnings, conflicts = [], []
    if degraded and fixture and metadata.get("golden_only") is not False:
        candidate, claim, _ = demo_dimensions(fixture, events, run["method"] == "demo_conflict")
        candidate = [versioned_rule_level(item, five_points) for item in candidate]
        if claim:
            claim = versioned_rule_level(claim, five_points)
        dimensions = list(candidate)
        provider, model = "demo", "scripted-fixtures-five-v1"
        warnings.append("使用固定 Demo 候选结果。")
    elif degraded and external_allowed:
        try:
            model_result, audit = semantic_evaluation(config["external_provider"], settings, task, events,
                                                      studychat=studychat, pilot=pilot, progress=progress,
                                                      rubric_version=config["rubric_version"])
        except Problem as exc:
            if pilot:
                raise
            warnings.append("外部语义评价未完成，已降级为规则评价。")
            audit = {"error": exc.code}
        except (OSError, ValueError) as exc:
            if pilot:
                raise Problem(503, "PROVIDER_TRANSPORT", "DeepSeek 网络响应未完成，未保存试跑报告。") from exc
            warnings.append("外部语义评价未完成，已降级为规则评价。")
            audit = {"error": "PROVIDER_TRANSPORT"}
        if model_result:
            provider, model = audit["provider"], audit["model"]
            lookup = {item.dimension: item.model_dump() for item in model_result.dimensions}
            candidate = [lookup[name] for name in DIMENSIONS]
            dimensions, degraded = list(candidate), False
            final = facts["final"][-1] if facts["final"] else None
            asserted = model_result.execution_claim
            if final and ((final in facts["failed"] and asserted == "passed") or
                          (facts["passed"] and asserted == "failed") or asserted == "not_run"):
                result = lookup[RESULT]
                claim = {"dimension": RESULT, "claim": asserted, "level": result["level"], "text": result["rationale"],
                         "event_ids": result["event_ids"]}
            elif run["method"] == "hybrid" and final and rule_result["state"] == "scored" and lookup[RESULT]["state"] != "scored":
                dimensions[list(DIMENSIONS).index(RESULT)] = rule_result
    elif degraded:
        warnings.append("未配置或未授权当前资料的语义模型，已降级为规则评价。")
    if fixture:
        warnings.append("模拟证据，仅用于工程演示")
    if studychat:
        warnings.append("StudyChat 过程评价依据对话中可观察的行为。归档缺少可核验的运行日志。")
    if pilot and pilot.get("out_of_task_event_ids"):
        warnings.append("资料核对发现部分对话与当前作业题目不匹配。页面保留这些消息，并标明评分范围。")

    if progress:
        progress("evidence")
    annotations = []
    if model_result:
        for item in model_result.annotations:
            annotations.append(annotate({"dimension": item.dimension, "level": None, "rationale": item.text,
                                         "event_ids": item.event_ids}, by_id, "llm", kind=item.type))
    else:
        annotations = [annotate(item, by_id, provider) for item in dimensions if item["event_ids"]]
    if claim and facts["final"]:
        decisive = facts["passed"][-1] if facts["passed"] else facts["final"][-1]
        rule_ids = [decisive["id"], facts["submission"]["id"]]
        conflicts.append({"state": "pending_review", "dimension": RESULT, "candidate": claim,
                          "rule_event_ids": rule_ids, "rule_result": rule_result})
        annotations = [item for item in annotations if item["dimension"] != RESULT]
        annotations.append(annotate({"dimension": RESULT, "level": None, "rationale": REASONS["CONFLICTING_RECORDS"],
                                     "event_ids": [*claim["event_ids"], *rule_ids]}, by_id, "rule", kind="rule_llm_conflict"))
    if facts["final_conflicts"]:
        warnings.append("同一提交快照存在相互矛盾的执行结果，请教师核对。")
        annotations = [item for item in annotations if item["dimension"] != RESULT or item["requires_review"]]
        annotations.append(annotate({"dimension": RESULT, "level": None, "rationale": REASONS["CONFLICTING_RECORDS"],
                                     "event_ids": facts["final_conflicts"]}, by_id, "rule", kind="record_conflict"))
    if conflicts or facts["final_conflicts"]:
        dimensions[list(DIMENSIONS).index(RESULT)] = special(RESULT, "CONFLICTING_RECORDS")

    if progress:
        progress("recommendations")
    recommendations = [item.model_dump() for item in model_result.teaching_recommendations] if model_result else []
    teachers = [item.model_dump() for item in model_result.teacher_recommendations] if model_result else []
    students = [item.model_dump() for item in model_result.student_recommendations] if model_result else []
    transfers = [item.model_dump() for item in model_result.transfer_tasks] if model_result else []
    if conflicts or facts["final_conflicts"]:
        recommendations = [item for item in recommendations if item["dimension"] != RESULT]
        teachers = [item for item in teachers if item["dimension"] != RESULT]
        students = [item for item in students if item["dimension"] != RESULT]
    report = {
        "run_id": run["id"], "session_id": session["id"], "source_type": session["source_type"],
        "method": run["method"], "purpose": run["purpose"], "degraded": degraded, "legacy_rubric": False,
        "dimensions": dimensions, "outcome": make_outcome(json.loads(task["rubric"]) or ITEMS, facts, fixture_items),
        "annotations": annotations, "warnings": warnings, "demo_only": bool(fixture) or run["purpose"] == "demo",
        "teaching_suggestions": [item["text"] for item in recommendations], "teaching_recommendations": recommendations,
        "teacher_recommendations": teachers, "student_recommendations": students,
        "transfer_tasks": transfers,
        "teaching_diagnosis": model_result.teaching_diagnosis.model_dump() if model_result else None,
        "ai_behaviors": [item.model_dump() for item in model_result.ai_behaviors] if model_result else [],
        "limitations": model_result.limitations if model_result else [], "conflicts": conflicts,
        "out_of_task_event_ids": pilot.get("out_of_task_event_ids", []) if pilot else [],
        "raw_rule_facts": rule_summary(facts), "raw_rule_dimensions": baseline,
        "raw_llm_candidate": {"dimensions": candidate, "claim": claim} if candidate else None,
        "provider_call": audit, "task_version": task["version"], "provider": provider, "model": model,
        "model_params_hash": digest(dump({"model": model, "temperature": 0,
                                          "max_tokens": audit.get("token_limit", 0) if audit else 0})),
        "config_version": run["config_version"],
        **{name: config[name] for name in ("rubric_version", "threshold_version", "prompt_version", "ruleset_version")},
    }
    if quality and model_result:
        verdict = audit["task_relevance"]
        report["studychat_quality"] = quality | {"relevance_reason": verdict["text"],
                                                 "relevance_evidence_event_ids": verdict["event_ids"]}
    grade = source_grade(metadata) if studychat and (not quality or quality["grade_link_valid"]) else None
    report.update(outcome_fields(report["outcome"]["score"], grade, studychat=studychat))
    recompute(report, config)
    if model_result:
        audit["candidate_process_score"] = model_result.process_score
    return report
