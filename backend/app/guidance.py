import json
from functools import lru_cache

from .core import ROOT


@lru_cache(maxsize=1)
def guidance_catalog():
    return json.loads((ROOT / "data" / "studychat-pilot-guidance.json").read_text(encoding="utf-8"))


def report_guidance(db, report, session_id, cutoff):
    if report.get("teacher_recommendations") or report.get("student_recommendations") or report.get("guidance_basis") == "teacher_edit":
        return report
    if report.get("provider") != "deepseek" or report.get("prompt_version") != "prompt-3.2-five-dimension":
        return report
    rows = guidance_catalog().get(session_id)
    if not rows:
        return report
    evidence = {row["sequence_no"]: row["id"] for row in db.execute("""SELECT id,sequence_no FROM events
        WHERE session_id=? AND sequence_no<=? AND type IN ('chat_user_message','chat_ai_message')""", (session_id, cutoff))}
    excluded = set(report.get("out_of_task_event_ids", []))
    teachers, students = [], []
    for dimension, numbers, observation, judgment, action, follow_up, next_action, example in rows:
        if not set(numbers) <= evidence.keys():
            continue
        ids = [evidence[number] for number in numbers]
        if excluded.intersection(ids):
            continue
        teachers.append({"dimension": dimension, "observation": observation, "judgment": judgment,
                         "action": action, "follow_up": follow_up, "event_ids": ids})
        students.append({"dimension": dimension, "action": next_action, "example": example, "event_ids": ids})
    return report | {"teacher_recommendations": teachers, "student_recommendations": students,
                     "guidance_basis": "archived_report_editorial"}
