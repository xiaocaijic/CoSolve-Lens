"""Author the immutable v1.0 engineering fixtures from the PRD acceptance cases."""
import hashlib
import json
from pathlib import Path

DEST = Path(__file__).resolve().parents[1] / "fixtures"
TITLE = "校园商店销售数据清洗与分析"
INSTRUCTIONS = "读取订单 CSV，校验必填值和日期，按订单号去重，保留退款并计算月净销售额，提交脚本、清洗数据、趋势图和说明。"

DIMENSIONS = ("organization", "verification", "diagnosis", "ai_integration")
CASES = {
    "F1": (
        [("u1", "u", "输入订单 CSV，输出清洗文件和月趋势。先处理缺失和日期，再去重、保留退款，最后核对边界。"),
         ("c1", "c", "date, deduplicate, refunds"), ("r1", "r", "ok"), ("t1", "t", "pass_boundary"), ("s1", "s", "核对退款月趋势和缺失行。")],
        [(3, "u1 c1"), (3, "r1 t1 s1"), (None, "NO_DIAGNOSTIC_EVENT"), (None, "NO_AI_USE")], [47, 18, 13, 12], 100.0,
    ),
    "F2": (
        [("u2a", "u", "输入订单 CSV，输出清洗文件和月趋势。保留退款；先日期和去重，再按月聚合和测试。"),
         ("a2", "a", "建议解析 amount 与日期后按月份聚合。"), ("c2a", "c", "initial adaptation"),
         ("t2a", "t", "fail"), ("u2b", "u", "定位：负数来自 refund_amount，当前把它映射到了销售额。"),
         ("c2b", "c", "map refunds separately"), ("t2b", "t", "pass_boundary"), ("s2", "s", "修正退款字段后重跑主要用例与边界。")],
        [(3, "u2a"), (3, "t2a t2b s2"), (3, "t2a u2b c2b t2b"), (3, "a2 c2a u2b c2b t2b")], [46, 18, 12, 12], 100.0,
    ),
    "F3": (
        [("u3", "u", "把完整答案给我。"), ("a3", "a", "读取订单并聚合。"), ("c3", "c", "aggregate orders"),
         ("s3", "s", "已粘贴方案。")],
        [(1, "u3"), (0, "c3 s3"), (None, "NO_DIAGNOSTIC_EVENT"), (1, "a3 c3")], [38, 13, 11, 8], 22.2,
    ),
    "F4": (
        [("r4a", "r", "KeyError: amount"), ("u4a", "u", "KeyError: amount"), ("a4a", "a", "请核对 CSV 表头。"),
         ("c4a", "c", "change display title"), ("r4b", "r", "KeyError: amount"), ("u4b", "u", "KeyError: amount"),
         ("s4", "s", "仍存在字段报错。")],
        [(1, "u4a u4b"), (1, "r4a r4b"), (0, "r4a c4a r4b"), (1, "a4a c4a")], [14, 9, 9, 8], 25.0,
    ),
    "F5": (
        [("a5", "a", "建议把所有负金额记录删除。"), ("c5a", "c", "discard negative amounts"), ("t5a", "t", "fail"),
         ("u5", "u", "负金额包含必须保留的退款。按订单状态保留退款，再根据日期汇总。"),
         ("c5b", "c", "retain refunds"), ("t5b", "t", "pass_boundary"), ("s5", "s", "纠正负值过滤规则并验证边界。")],
        [(2, "u5"), (3, "t5a t5b s5"), (3, "t5a u5 c5b t5b"), (3, "a5 c5a u5 c5b t5b")], [45, 17, 12, 11], 91.7,
    ),
    "F6": (
        [("u6", "u", "CSV 到净销售趋势，保留退款。先检查日期和重复订单，然后聚合并验证结果。"),
         ("a6", "a", "按月分组前先验证日期。"), ("c6a", "c", "initial month aggregation"),
         ("r6a", "r", "ValueError: date"), ("u6b", "u", "异常日期造成解析失败，先分离非法日期再汇总。"),
         ("c6b", "c", "separate invalid dates"), ("t6", "t", "partial"), ("s6", "s", "已交数据，边界仍待修复。")],
        [(3, "u6"), (2, "t6 s6"), (2, "r6a u6b c6b t6"), (2, "a6 c6a c6b")], [31, 12, 9, 8], 75.0,
    ),
}
TYPES = {"u": "chat_user_message", "a": "chat_ai_message", "c": "code_snapshot",
         "r": "run_finished", "t": "test_finished", "s": "submission_created"}


def sha(content):
    return hashlib.sha256(content).hexdigest()


def build(fixture_id, steps):
    events, artifacts = [], []
    code_version = 0

    def code_artifact(description):
        nonlocal code_version
        code_version += 1
        content = f"# {fixture_id} fixture: {description}\n"
        artifact = {"external_artifact_id": f"code-v{code_version}", "kind": "code", "version": code_version,
                    "content": content, "mime_type": "text/x-python"}
        artifacts.append(artifact)
        return artifact

    artifact = None
    for index, (event_id, kind, body) in enumerate(steps, 1):
        payload = {"text": body}
        if kind == "c":
            artifact = code_artifact(body)
            payload = {"language": "python", "content": artifact["content"], "version": code_version,
                       "artifact_checksum": sha(artifact["content"].encode())}
        elif kind in ("r", "t"):
            if artifact is None:
                artifact = code_artifact("initial run")
            payload = dict(artifact_id=artifact["external_artifact_id"], artifact_version=code_version,
                           artifact_checksum=sha(artifact["content"].encode()), execution_mode="sandbox",
                           synthetic=False, verified=True)
            if kind == "r":
                payload.update(exit_code=0 if body == "ok" else 1, stdout="完成主流程" if body == "ok" else "",
                               stderr="" if body == "ok" else body)
            else:
                cases = [{"id": "main", "passed": body != "fail", "category": "main"}]
                if body in ("pass_boundary", "partial"):
                    cases.append({"id": "refund-boundary", "passed": body == "pass_boundary", "category": "boundary"})
                passed = sum(case["passed"] for case in cases)
                payload.update(passed=passed, failed=len(cases) - passed, cases=cases)
        elif kind == "s":
            refs = [artifact["external_artifact_id"]]
            for artifact_kind, content, mime in [
                ("cleaned_data", "month,net\n2026-01,20\n", "text/csv"),
                ("trend", "month,net\n2026-01,20\n", "text/csv"),
                ("statement", body, "text/plain"),
            ]:
                if fixture_id == "F6" and artifact_kind == "trend":
                    continue
                artifacts.append({"external_artifact_id": artifact_kind, "kind": artifact_kind, "version": 1,
                                  "content": content, "mime_type": mime})
                refs.append(artifact_kind)
            payload = {"artifact_refs": refs, "artifact_checksum": sha(artifact["content"].encode()), "statement": body}
        events.append({"external_event_id": event_id, "sequence_no": index, "occurred_at": None,
                       "type": TYPES[kind], "payload": payload})
    return {"schema_version": "1.0", "source": {"name": "工程验收样例", "license_note": "合成演示记录"},
            "sessions": [{"external_session_id": fixture_id, "participant_key": f"demo-{fixture_id.lower()}",
                          "task": {"external_task_id": "clean-sales-v1", "title": TITLE, "instructions": INSTRUCTIONS},
                          "events": events, "artifacts": artifacts}]}


def expected(dimensions, items, process):
    outcome = sum(items)
    if process >= 67:
        diagnosis = "过程与成果均达标" if outcome >= 75 else "过程达标、成果需完善"
    else:
        diagnosis = "过程需加强、成果达标" if outcome >= 75 else "过程与成果均需支持"
    return {
        "dimensions": [{"dimension": name, "level": level,
                        "state": "scored" if level is not None else "not_applicable",
                        "reason": None if level is not None else refs,
                        "event_keys": refs.split() if level is not None else []}
                       for name, (level, refs) in zip(DIMENSIONS, dimensions, strict=True)],
        "outcome_items": items, "outcome_score": outcome, "process_score": process, "diagnosis": diagnosis,
        "ui": {"demo_badge": "模拟证据，仅用于工程演示", "evidence_links": True,
               "shows_na": any(level is None for level, _ in dimensions), "shows_zero": any(level == 0 for level, _ in dimensions)},
    }


def rules_expected(fixture_id):
    verification = {"F1": (3, "r1 t1 s1"), "F2": (3, "t2b s2"), "F3": (0, "c3 s3"),
                    "F4": (1, "r4b s4"), "F5": (3, "t5b s5"), "F6": (2, "t6 s6")}[fixture_id]
    no_error = fixture_id in {"F1", "F3"}
    no_ai = fixture_id == "F1"
    return {"dimensions": [
        {"dimension": "organization", "state": "insufficient_evidence", "level": None},
        {"dimension": "verification", "state": "scored", "level": verification[0], "event_keys": verification[1].split()},
        {"dimension": "diagnosis", "state": "not_applicable" if no_error else "insufficient_evidence", "level": None},
        {"dimension": "ai_integration", "state": "not_applicable" if no_ai else "insufficient_evidence", "level": None},
    ], "process_score": None, "diagnosis": None}


def main():
    files = {}
    for fixture_id, (steps, dims, items, score) in CASES.items():
        files[fixture_id + "_input.json"] = build(fixture_id, steps)
        files[fixture_id + "_demo_expected.json"] = expected(dims, items, score)
        files[fixture_id + "_rules_expected.json"] = rules_expected(fixture_id)
    files["F5C_input.json"] = files["F5_input.json"]
    files["F5C_reviewed_expected.json"] = files["F5_demo_expected.json"]
    initial = expected(*CASES["F5"][1:])
    initial["dimensions"][1].update(level=None, state="insufficient_evidence", reason="CONFLICTING_RECORDS",
                                    event_keys=[])
    initial.update(process_score=None, diagnosis=None, conflict={"claim_evidence": ["t5a"], "rule_evidence": ["t5b", "s5"]})
    files["F5C_initial_expected.json"] = initial
    DEST.mkdir(exist_ok=True)
    hashes = {}
    for name, data in files.items():
        content = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        (DEST / name).write_bytes(content)
        hashes[name] = sha(content)
    (DEST / "manifest.json").write_bytes(
        (json.dumps({"fixture_version": "1.0", "files": hashes}, indent=2, sort_keys=True) + "\n").encode()
    )


if __name__ == "__main__":
    main()
