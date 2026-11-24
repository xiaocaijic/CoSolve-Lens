"""Read the fixed local StudyChat v1.1 snapshot without extracting or executing submissions."""
import csv
import hashlib
import heapq
import json
import math
import os
import re
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from .adapters import prepare_box
from .core import ROOT, Problem, digest, dump

TERMS = ("f24", "s25")
ASSIGNMENTS = {f"a{number}" for number in range(1, 8)}
INPUT_KEYS = {"prompt", "response", "topic", "chatId", "userId", "timestamp", "interactionCount", "llm_label"}
SUBMISSION_TYPES = {".py": "text/x-python", ".ipynb": "application/x-ipynb+json",
                    ".md": "text/markdown", ".csv": "text/csv", ".json": "application/json"}
ASSIGNMENT_DIRECTORY = re.compile(r"assignment-([1-7])-[a-z0-9-]+-submissions")
USER_DIRECTORY = re.compile(r"user_[a-fA-F0-9-]{36}")
MAX_ARCHIVE_CONTENT = 256_000_000
MAX_ARTIFACT_CONTENT = 1_048_576


def snapshot_root():
    if configured := os.getenv("AI_COLLAB_STUDYCHAT_ROOT"):
        return Path(configured)
    adjacent = ROOT.parent / "data" / "StudyChat"
    return adjacent if adjacent.is_dir() else ROOT / "data" / "StudyChat"


def file_hash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def assignment_path(root, term, topic):
    folder = root / "assignment_text" / f"studychat_{term}_assignments" / topic
    if not folder.is_dir():
        return None
    return next((file for file in sorted(folder.iterdir()) if file.name.casefold() == "readme.md" and file.is_file()), None)


def grade_rows(root, term):
    file = root / "scores" / f"{term}_grades_released_normalized.csv"
    if not file.is_file():
        return {}, None
    rows = {}
    with file.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not {"userId", "directory_name"} <= set(reader.fieldnames or ()):
            raise Problem(422, "STUDYCHAT_HEADERS", "StudyChat 成绩文件需要 userId 和 directory_name 表头。")
        for row in reader:
            if row["userId"] in rows:
                raise Problem(422, "STUDYCHAT_SCORE_KEYS", "同一学期的成绩出现重复参与者键。")
            rows[row["userId"]] = row
    return rows, file


def file_sources(root, terms, assignment):
    assignments, scores, hashes = {}, {}, {}
    for term in terms:
        scores[term], file = grade_rows(root, term)
        if file:
            hashes[str(file.relative_to(root))] = file_hash(file)
        for topic in sorted(ASSIGNMENTS if assignment == "all" else {assignment}):
            if path := assignment_path(root, term, topic):
                contents = path.read_bytes()
                if len(contents) > MAX_ARTIFACT_CONTENT:
                    raise Problem(422, "STUDYCHAT_ASSIGNMENT_SIZE", "作业说明超过单份文本大小上限。")
                assignments[term, topic] = contents.decode("utf-8-sig")
                hashes[str(path.relative_to(root))] = digest(contents)
    return assignments, scores, hashes


def as_timestamp(value):
    if type(value) is not int or not 1_000_000_000_000 <= value < 10_000_000_000_000:
        return None
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat()


def matching_dialogue_files(root, terms):
    combined = root / "data.jsonl"
    dialogues = [(term, root / "dialogues" / f"{term}_labeled_dialogues.jsonl") for term in terms]
    if len(terms) == 2 and combined.is_file():
        return [(None, combined)]
    if all(path.is_file() for _, path in dialogues):
        return dialogues
    if combined.is_file():
        return [(None, combined)]
    raise Problem(404, "STUDYCHAT_FILES_MISSING", "未找到所选 StudyChat 本地对话快照。")


def read_turns(root, terms, assignment, hashes, stats):
    conversations = defaultdict(dict)
    for term_hint, file in matching_dialogue_files(root, terms):
        file_digest = hashlib.sha256()
        with file.open("rb") as lines:
            for line_no, line in enumerate(lines, 1):
                file_digest.update(line)
                if not line.strip():
                    continue
                try:
                    row = json.loads(line.decode("utf-8-sig" if line_no == 1 else "utf-8"))
                    if not isinstance(row, dict) or not INPUT_KEYS <= row.keys():
                        raise ValueError
                    term = row.get("semester", term_hint)
                    if term not in TERMS:
                        raise ValueError
                    if term not in terms or (assignment != "all" and row["topic"] != assignment):
                        continue
                    if (row["topic"] not in ASSIGNMENTS or not isinstance(row["userId"], str) or not row["userId"].strip()
                        or not isinstance(row["chatId"], str) or not row["chatId"].strip() or
                        type(row["interactionCount"]) is not int or row["interactionCount"] < 0 or
                        not all(isinstance(row[key], str) for key in ("prompt", "response"))):
                        raise ValueError
                except (UnicodeError, ValueError, TypeError):
                    raise Problem(422, "STUDYCHAT_DIALOGUE_SCHEMA", "本地对话文件的字段与 StudyChat v1.1 不符。",
                                  {"file": file.name, "line": line_no}) from None
                stats["interactions"] += 1
                stats[term] += 1
                key = term, row["userId"], row["topic"]
                turn_key = row["chatId"], row["interactionCount"]
                if turn_key in conversations[key]:
                    raise Problem(422, "STUDYCHAT_TURN_KEYS", "同一对话有重复轮次，导入已停止。", {"line": line_no})
                label = row["llm_label"]
                label = label.get("label") if isinstance(label, dict) else None
                conversations[key][turn_key] = (row["timestamp"], row["prompt"], row["response"], label,
                                                row.get("chatTotalInteractionCount"), row.get("chatTitle"))
        hashes[str(file.relative_to(root))] = file_digest.hexdigest()
    return conversations


def source_events(turns):
    def order(task):
        (_, turn), (stamp, *_texts) = task
        return as_timestamp(stamp) or "", turn

    grouped = defaultdict(list)
    for step in turns.items():
        grouped[step[0][0]].append(step)
    chat_queues = [sorted(entries, key=lambda entry: entry[0][1]) for entries in grouped.values()]
    ordered = heapq.merge(*chat_queues, key=order)
    events = []
    chat_numbers = {}
    for (chat_id, turn), (stamp, prompt, response, label, declared, title) in ordered:
        conversation = chat_numbers.setdefault(chat_id, len(chat_numbers) + 1)
        for role, text, event_time in (("user", prompt, as_timestamp(stamp)), ("ai", response, None)):
            seq = len(events) + 1
            payload = {"text": text, "source_turn": turn, "conversation": conversation}
            if role == "user" and isinstance(label, str):
                payload["source_labels"] = [label]
            if role == "user":
                payload["studychat_source"] = {"chat_id": chat_id, "declared_turns": declared, "title": title or ""}
            events.append({"external_event_id": str(seq), "sequence_no": seq, "occurred_at": event_time,
                           "type": "chat_" + role + "_message", "payload": payload})
    return events


def assignment_scores(row, topic):
    result = {}
    for field in (topic, "e1", "e2", "e3", "llm", "no_llm"):
        try:
            value = float(row.get(field, "") or "")
        except ValueError:
            continue
        if math.isfinite(value):
            result["assignment_normalized" if field == topic else field + "_normalized"] = value
    return result


def archive_index(archive, scores, groups, stats):
    crosswalk = {}
    for term, rows in scores.items():
        for peer, row in rows.items():
            directory = row.get("directory_name", "")
            if USER_DIRECTORY.fullmatch(directory or ""):
                key = term, directory
                if key in crosswalk and crosswalk[key] != peer:
                    raise Problem(422, "STUDYCHAT_SUBMISSION_KEYS", "评分文件的压缩包目录关联不唯一。")
                crosswalk[key] = peer
    found = defaultdict(list)
    for item in archive.infolist():
        stats["archive_entries"] += 1
        parts = PurePosixPath(item.filename).parts
        if item.is_dir() or len(parts) < 5 or any(part in ("/", ".", "..") or "\\" in part for part in parts):
            continue
        if any(part.startswith(".") or part == "__MACOSX" for part in parts):
            continue
        term, spec, directory = parts[1:4]
        match = ASSIGNMENT_DIRECTORY.fullmatch(spec)
        peer = crosswalk.get((term, directory))
        suffix = PurePosixPath(parts[-1]).suffix.lower()
        key = (term, peer, f"a{match[1]}") if match and peer else None
        if key not in groups or suffix not in SUBMISSION_TYPES:
            continue
        if item.file_size > MAX_ARTIFACT_CONTENT:
            stats["oversized_archive_files"] += 1
            continue
        found[key].append((item, suffix))
    return found


def attach_submission(archive, matches, record, stats):
    if len(matches) > 500:
        raise Problem(422, "STUDYCHAT_TOO_MANY_ARTIFACTS", "某会话关联的提交文件过多。")
    refs = []
    for ordinal, (item, suffix) in enumerate(sorted(matches, key=lambda pair: pair[0].filename), 1):
        if stats["archive_text_bytes"] + item.file_size > MAX_ARCHIVE_CONTENT:
            raise Problem(422, "STUDYCHAT_ARCHIVE_LIMIT", "可关联提交资料超过本地适配器的读取预算。请按学期分批导入。")
        with archive.open(item) as payload:
            data = payload.read(MAX_ARTIFACT_CONTENT + 1)
        if len(data) > MAX_ARTIFACT_CONTENT:
            raise Problem(422, "STUDYCHAT_ARCHIVE_LIMIT", "压缩包内文本超过大小上限。")
        try:
            text = data.decode("utf-8-sig")
        except UnicodeError:
            stats["unreadable_archive_files"] += 1
            continue
        name = "submission-file-" + str(ordinal)
        record["artifacts"].append({"external_artifact_id": name, "kind": "submission_file",
                                    "version": ordinal, "content": text, "mime_type": SUBMISSION_TYPES[suffix]})
        stats["linked_files"] += 1
        stats["archive_text_bytes"] += len(data)
        refs.append(name)
    if refs:
        stats["sessions_with_submissions"] += 1
        seq = len(record["events"]) + 1
        record["events"].append({"external_event_id": str(seq), "sequence_no": seq, "type": "submission_created",
                                 "payload": {"artifact_refs": refs, "provenance": "studychat_assignment_archive"}})


def load_snapshot(settings, metadata, order):
    root = snapshot_root()
    terms = TERMS if order["semester"] == "all" else (order["semester"],)
    topic = order["assignment"]
    stats = Counter()
    try:
        instructions, scores, hashes = file_sources(root, terms, topic)
        groups = read_turns(root, terms, topic, hashes, stats)
        records = {}
        for (term, peer, task), turns in groups.items():
            grades = assignment_scores(scores[term].get(peer, {}), task)
            if grades:
                stats["sessions_with_grades"] += 1
            records[term, peer, task] = {
                "external_session_id": dump([term, peer, task]), "participant_key": peer,
                "task": {"external_task_id": term + "-" + task, "title": term.upper() + " · " + task.upper(),
                         "instructions": instructions.get((term, task), "")},
                "events": source_events(turns), "artifacts": [], "external_scores": grades,
            }
        zip_path = root / "submissions" / "studychats_student_submissions_cleaned_final.zip"
        if zip_path.is_file():
            with zipfile.ZipFile(zip_path) as archive:
                for key, matches in archive_index(archive, scores, groups, stats).items():
                    attach_submission(archive, matches, records[key], stats)
            hashes[str(zip_path.relative_to(root))] = file_hash(zip_path)
    except (zipfile.BadZipFile, OSError, UnicodeError):
        raise Problem(422, "STUDYCHAT_SNAPSHOT_UNREADABLE", "本地快照的对话、说明、成绩或提交压缩包无法读取。") from None
    source_version = digest(dump({"files": hashes, "selection": order, "adapter": "studychat-1.1"}))
    metadata["name"] = metadata["name"] or "StudyChat v1.1 · " + order["semester"].upper() + " / " + topic.upper()
    normalized, preview = prepare_box(settings, "studychat", metadata, {
        "schema_version": "1.0", "source": {}, "sessions": list(records.values())
    }, source_version)
    preview["snapshot"] = {"version": "StudyChat v1.1", "files": sorted(hashes), "stats": dict(stats),
                           "reports_run_log_present": False}
    return normalized, preview
