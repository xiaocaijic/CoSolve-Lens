"""Local eligibility checks and immutable-report quarantine for StudyChat."""
import json
import re
from collections import defaultdict
from functools import lru_cache

from .core import ROOT, digest, dump, now


MIN_TURNS = 4
CHAT_TYPES = ("chat_user_message", "chat_ai_message")
FORMATTING = re.compile(r"\b(?:convert|format|transform|turn)\b[^.!?\n]{0,180}\b(?:markdown|csv|json|table)\b", re.I)
IDLE = re.compile(r"\s*(?:hi|hello|hey|thanks(?: a lot)?|thank you|good (?:morning|evening)|here it is|"
                  r"wait(?: a moment)?|please wait|one (?:sec(?:ond)?|moment)|ok(?:ay)?|"
                  r"你好|谢谢|等一下|稍等|好的|发给你)[\s.!,:?，。！：？]*", re.I)
SIGNALS = {
    "质疑": r"\b(?:why|is (?:it|this) (?:right|correct)|are you sure|doesn.t)\b|为什么|是否正确|质疑",
    "比较": r"\b(?:compare|difference|versus|instead of|rather than|better than)\b|比较|区别|相比",
    "解释": r"\b(?:because|explain|how does|what does|therefore)\b|因为|解释|原因",
    "诊断": r"\b(?:error|debug|wrong|traceback|unexpected|not working)\b|报错|异常|诊断",
    "修改": r"\b(?:change|revise|modify|adapt|fix|update)\b|修改|调整|改成",
    "验证": r"\b(?:verify|validate|test|check|counterexample)\b|验证|核对|反例",
}
RICH = {label: re.compile(pattern, re.I) for label, pattern in SIGNALS.items()}
TOPICS = (
    (r"future of work", ("future of work", "film", "documentary", "automation", "manual labor")),
    (r"search\s*complete", ("prefix", "autocomplete", "trie", "dfs", "bfs", "ucs", "priority queue", "search strategy")),
    (r"data cleaning|preprocessing", ("deduplicate", "missing values", "impute", "cleaning", "categorical", "numerical")),
    (r"sklearn|scikit-learn|judging flowers|equation of a slime", ("sklearn", "scikit-learn", "classifier", "train_test_split", "accuracy", "logistic regression", "classification", "confusion matrix", "temperature", "linear regression")),
    (r"bayes complete|n-gram language", ("n-gram", "ngram", "bayes", "conditional probability", "bigrams", "trigrams", "corpus", "tokeniz")),
    (r"neural complete", ("rnn", "lstm", "embedding", "pytorch", "neural network", "softmax", "hidden state", "cross entropy")),
)


@lru_cache(maxsize=1)
def provenance():
    path = ROOT / "data" / "studychat-v1.1.provenance.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def quality_check(task, events, *, grade_matched, submission_matched):
    groups = defaultdict(lambda: defaultdict(dict))
    duplicates = set()
    for event in events:
        if event["type"] in CHAT_TYPES:
            payload = event["payload"]
            conversation = payload.get("conversation", 1)
            turn = groups[conversation][payload.get("source_turn", -1)]
            if event["type"] in turn:
                duplicates.add(conversation)
            turn[event["type"]] = event
    observed, declared, all_declared = 0, 0, bool(groups)
    transcripts, chats = [], []
    for conversation, turns in groups.items():
        indices = sorted(turns)
        observed += len(indices)
        incomplete = conversation in duplicates or indices != list(range(len(indices)))
        texts, prompts, event_ids = [], [], []
        for index in indices:
            turn = turns[index]
            pair = [turn.get(kind, {}).get("payload", {}).get("text", "") for kind in CHAT_TYPES]
            texts.append((index, *pair))
            incomplete |= not all(text.strip() for text in pair)
            event_ids.extend(item["id"] for item in turn.values())
            if CHAT_TYPES[0] in turn:
                prompts.append(turn[CHAT_TYPES[0]])
        metadata = provenance().get(digest(dump(texts)))
        if not metadata:
            # New imports carry the same provenance directly from the source.
            source = turns[indices[0]].get(CHAT_TYPES[0], {}).get("payload", {}).get("studychat_source", {})
            if source:
                metadata = [source["chat_id"], source.get("declared_turns"), source.get("title", ""), source.get("inconsistent", False)]
        count, title = None, ""
        if metadata:
            chat_id, count, title, inconsistent = metadata[:4]
            chats.append(chat_id)
            incomplete |= inconsistent or type(count) is int and len(indices) < count
        if type(count) is int:
            declared += count
        else:
            all_declared = False
        transcripts.append({"conversation": conversation, "observed_turns": len(indices), "declared_turns": count,
                            "incomplete": bool(incomplete), "title": title, "prompts": prompts, "event_ids": event_ids})

    instructions = task["instructions"]
    heading = next((line for line in instructions.splitlines() if line.startswith("# ")), "")
    terms = next((words for pattern, words in TOPICS if re.search(pattern, heading, re.I)), ())
    relevant, raw_relevant, conversion, included, excluded = [], [], [], [], []
    rich = {}
    title_support, idle_only = False, True
    for chat in transcripts:
        prompts = chat.pop("prompts")
        title = chat.pop("title")
        ids = chat.pop("event_ids")
        title_support |= any(term in title.casefold() for term in terms)
        idle_only &= all(IDLE.fullmatch(item["payload"]["text"]) for item in prompts)
        matches = [item["id"] for item in prompts if any(term in item["payload"]["text"].casefold() for term in terms)]
        raw_relevant.extend(matches)
        conversion.extend(item["id"] for item in prompts if FORMATTING.search(item["payload"]["text"]) or
                          any("Code/Data Conversion" in tag for tag in item["payload"].get("source_labels", [])))
        if chat["incomplete"] or chat["declared_turns"] is None or not matches:
            chat["in_scope"] = False
            excluded.extend(ids)
            continue
        chat["in_scope"] = True
        included.extend(ids)
        relevant.extend(matches)
        for item in prompts:
            for label, pattern in RICH.items():
                if pattern.search(item["payload"]["text"]):
                    rich.setdefault(label, []).append(item["id"])
    richness = [{"signal": label, "event_ids": ids[:8]} for label, ids in rich.items()]
    if relevant or raw_relevant:
        relevance, reason, refs = "relevant", "学生对话中出现与归档题目相符的专门概念或任务内容。", relevant or raw_relevant
    elif conversion and not title_support and re.search(r"future of work", heading, re.I):
        relevance, reason, refs = "irrelevant", "实际请求是转换数据格式，和影片及未来工作主题无关。", conversion
    else:
        relevance, reason, refs = "uncertain", "本地线索不足以确认对话对应归档作业。", []
    reasons = []
    if not groups or not included and any(chat["incomplete"] for chat in transcripts):
        reasons.append("incomplete")
    if relevance == "irrelevant":
        reasons.append("off_task")
    scoped_turns = sum(chat["observed_turns"] for chat in transcripts if chat["in_scope"])
    if (scoped_turns if included else observed) < MIN_TURNS or (not richness and (included or conversion or idle_only)):
        reasons.append("too_short")
    if relevance == "uncertain" or (not included and not reasons):
        reasons.append("uncertain")
    status = reasons[0] if reasons else "eligible"
    linked = status == "eligible" and relevance == "relevant"
    return {"eligibility_status": status, "observed_turns": observed, "declared_turns": declared if all_declared else None,
            "completeness_ratio": round(min(1, observed / declared), 4) if all_declared and declared else None,
            "task_relevance": relevance, "relevance_reason": reason, "relevance_evidence_event_ids": refs[:8],
            "richness_signals": richness, "grade_matched": grade_matched, "submission_matched": submission_matched,
            "grade_link_valid": linked and grade_matched, "submission_link_valid": linked and submission_matched,
            "exclusion_reasons": reasons, "source_chat_ids": chats, "transcripts": transcripts,
            "scoped_event_ids": included, "excluded_event_ids": excluded, "scoped_turns": scoped_turns}


def session_quality(db, session, task, events):
    from .outcomes import source_grade

    grade = source_grade(json.loads(session["metadata"]))
    stored = {row[0] for row in db.execute("SELECT id FROM artifacts WHERE session_id=?", (session["id"],))}
    submitted = {ref for event in events if event["type"] == "submission_created"
                 for ref in event["payload"].get("artifact_refs", [])}
    return quality_check(task, events, grade_matched=grade is not None, submission_matched=bool(stored & submitted))


def historical_model_valid(report):
    audit = report.get("provider_call") or {}
    stages = audit.get("stages", [])
    return (report.get("provider") == "deepseek" and not report.get("degraded")
            and any(stage.get("stage") in {"summary", "complete_dialogue"} for stage in stages)
            and all(stage.get("id") for stage in stages))


def cited_dialogue_ids(report):
    refs = {event_id for key in ("dimensions", "teacher_recommendations", "student_recommendations", "transfer_tasks",
                                 "teaching_recommendations", "ai_behaviors")
            for item in report.get(key, []) for event_id in item.get("event_ids", [])}
    refs.update(ref["event_id"] for item in report["annotations"] if item["type"] != "ai_context_issue"
                for ref in item["evidence_refs"])
    refs.update((report.get("teaching_diagnosis") or {}).get("event_ids", []))
    return refs


def quarantine_existing(db):
    from .evaluation import event_payloads
    from .rubrics import FIVE_DIMENSION_VERSIONS
    from .studychat_policy import IDENTITY

    rows = db.execute("""SELECT rv.id,rv.body,rv.session_id,r.event_cutoff
        FROM report_versions rv JOIN sessions s ON s.id=rv.session_id JOIN data_sources d ON d.id=s.source_id
        JOIN evaluation_runs r ON r.id=rv.run_id
        WHERE d.adapter='studychat' AND d.source_identity IN (?,?)
          AND json_extract(rv.body,'$.rubric_version') IN (?,?)
          AND NOT EXISTS (SELECT 1 FROM studychat_quality_rejections q WHERE q.report_id=rv.id)""",
        (IDENTITY, "studychat-v1.1", *FIVE_DIMENSION_VERSIONS)).fetchall()
    for row in rows:
        report = json.loads(row["body"])
        session = db.execute("SELECT * FROM sessions WHERE id=?", (row["session_id"],)).fetchone()
        task = db.execute("SELECT instructions FROM task_definitions WHERE id=?", (session["task_id"],)).fetchone()
        quality = session_quality(db, session, task, event_payloads(db, session["id"], row["event_cutoff"]))
        reasons = [*quality["exclusion_reasons"]]
        if set(quality["excluded_event_ids"]) & cited_dialogue_ids(report):
            reasons.append("旧报告引用了本轮质量规则排除的原始对话")
        if not historical_model_valid(report):
            reasons.append("未验证的 DeepSeek 返回")
        if reasons:
            db.execute("INSERT INTO studychat_quality_rejections VALUES (?,?,?)", (row["id"], dump(reasons), now()))
            db.execute("DELETE FROM effective_reports WHERE report_id=?", (row["id"],))
            db.execute("UPDATE exports SET expires_at=0 WHERE source_id=?", (session["source_id"],))
