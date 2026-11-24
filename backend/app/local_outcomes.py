from decimal import Decimal, ROUND_HALF_UP

from pydantic import BaseModel, Field

from .core import Problem


RUBRIC_VERSION = "local-outcome-1.0"
CRITERIA = (
    {"item_id": "deliverable", "title": "交付与任务要求", "weight": 25, "guide": "核对交付物是否齐全，是否满足任务规定的格式和限制。"},
    {"item_id": "correctness", "title": "任务结果正确性", "weight": 45, "guide": "查看实际结果，抽查关键样例或可人工核验的结果。无法核验时留空。"},
    {"item_id": "explanation", "title": "说明与可核查性", "weight": 30, "guide": "核对提交中是否解释了关键决策，并能从材料中追溯结果。"},
)


class LocalOutcomeItem(BaseModel):
    item_id: str
    score: int | None = Field(ge=0, le=100, strict=True)
    reason: str = Field(min_length=1, max_length=2000)
    artifact_ids: list[str] = Field(default_factory=list, max_length=30)


def outcome_artifacts(db, session_id, events, report):
    quality = report.get("studychat_quality")
    if report["demo_only"] or (quality and not quality["submission_link_valid"]):
        return {}, None
    submission = next((item for item in reversed(list(events)) if item["type"] == "submission_created"), None)
    if submission is None:
        return {}, None
    references = set(submission["payload"].get("artifact_refs", []))
    files = db.execute("""SELECT id, kind, version, mime_type FROM artifacts
                       WHERE session_id=? AND TRIM(content) <> '' ORDER BY kind, version""", (session_id,))
    return {row["id"]: dict(row) for row in files if row["id"] in references}, submission["id"]


def assess_outcome(items, artifacts, submission_id, validate_reason):
    if not artifacts:
        raise Problem(422, "LOCAL_OUTCOME_ARTIFACTS", "当前报告没有可核对的正式提交材料。")
    provided = {item.item_id: item for item in items}
    if len(items) != len(CRITERIA) or set(provided) != {item["item_id"] for item in CRITERIA}:
        raise Problem(422, "LOCAL_OUTCOME_RUBRIC", "请逐项填写本地成果量规。")
    results = []
    for criterion in CRITERIA:
        item = provided[criterion["item_id"]]
        refs = list(dict.fromkeys(item.artifact_ids))
        if not set(refs) <= artifacts.keys() or (item.score is not None and not refs):
            raise Problem(422, "LOCAL_OUTCOME_EVIDENCE", "每个已评分项目须引用本次提交的真实材料。")
        results.append({**criterion, "score": item.score, "max_score": 100,
                        "reason": validate_reason(item.reason), "artifact_ids": refs,
                        "evidence_refs": [submission_id] if refs else [],
                        "state": "scored" if item.score is not None else "insufficient_evidence"})
    total = None
    if all(item["score"] is not None for item in results):
        weighted = sum(item["score"] * item["weight"] for item in results)
        total = float((Decimal(weighted) / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    return {"score": total, "complete": total is not None, "items": results,
            "basis": "local_review", "rubric_version": RUBRIC_VERSION}
