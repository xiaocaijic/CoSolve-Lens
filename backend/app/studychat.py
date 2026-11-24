"""Mapping adapter for a teacher-provided UTF-8 turn table.

Validated with fixtures/studychat_turns.csv. No restricted release has been
obtained or tested. The caller supplies column mappings after reading headers.
"""
import csv
import io
from collections import OrderedDict

from .core import Problem

REQUIRED = {"participant", "task", "session", "role", "text"}
OPTIONAL = {"turn", "labels", "score", "task_title", "instructions"}
ROLES = {"user": "chat_user_message", "student": "chat_user_message",
         "assistant": "chat_ai_message", "ai": "chat_ai_message"}


def adapt(data, filename, mapping):
    if not filename.lower().endswith(".csv"):
        raise Problem(422, "STUDYCHAT_FILE_TYPE", "StudyChat 映射目前接受单个 UTF-8 对话轮次 CSV。")
    try:
        reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig"), newline=""))
        fields = reader.fieldnames
        if not fields or len(fields) != len(set(fields)):
            raise ValueError
        if (not mapping or not REQUIRED <= mapping.keys() or set(mapping) - REQUIRED - OPTIONAL
            or any(value not in fields for value in mapping.values())):
            raise Problem(422, "STUDYCHAT_MAPPING_REQUIRED", "已识别 CSV 表头，请明确映射必填字段后重试。",
                          {"headers": [field[:100] for field in fields[:50]],
                           "required": sorted(REQUIRED), "optional": sorted(OPTIONAL)})

        def value(row, field):
            return (row.get(mapping.get(field), "") or "").strip()

        grouped = OrderedDict()
        for index, row in enumerate(reader, 2):
            participant, assignment, session_id, role = (value(row, field) for field in ("participant", "task", "session", "role"))
            if not participant or not assignment or not session_id or role.lower() not in ROLES:
                raise Problem(422, "STUDYCHAT_ROW", "轮次行缺少必填标识或角色不在支持列表。",
                              {"line": index})
            if session_id not in grouped:
                grouped[session_id] = {
                    "external_session_id": session_id, "participant_key": participant, "task": {
                        "external_task_id": assignment, "title": value(row, "task_title") or "StudyChat 来源任务",
                        "instructions": value(row, "instructions")},
                    "events": [], "artifacts": [], "external_scores": {},
                }
            session = grouped[session_id]
            if participant != session["participant_key"] or assignment != session["task"]["external_task_id"]:
                raise Problem(422, "STUDYCHAT_ROW", "同一会话内的参与者和任务字段不一致。", {"line": index})
            payload = {"text": value(row, "text")}
            if labels := value(row, "labels"):
                payload["source_labels"] = labels
            seq = int(value(row, "turn")) if value(row, "turn") else len(session["events"]) + 1
            session["events"].append({"external_event_id": "turn-" + str(seq), "sequence_no": seq,
                                      "type": ROLES[role.lower()], "payload": payload})
            if score := value(row, "score"):
                session["external_scores"]["assignment_score"] = float(score)
        for session in grouped.values():
            session["events"].sort(key=lambda item: item["sequence_no"])
        return {"schema_version": "1.0", "source": {}, "sessions": list(grouped.values())}
    except (ValueError, UnicodeError, csv.Error):
        raise Problem(422, "STUDYCHAT_CSV", "CSV 表头、轮次序号或来源成绩格式不正确。") from None
