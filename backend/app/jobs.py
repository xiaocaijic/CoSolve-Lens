import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from .adapters import persist_import, persist_snapshot_import
from .core import Problem, database, digest, dump, get_session, get_source, now, uid
from .evaluation import active_config, evaluate, event_payloads, store_report

LOGGER = logging.getLogger(__name__)
TERMINAL = {"completed", "partial", "failed", "cancelled"}
LEASE_SECONDS = 30


def enqueue(db, user_id, kind, input_data, total, *, source_id=None, session_id=None):
    input_hash = digest(dump(input_data))
    existing = db.execute(
        "SELECT * FROM jobs WHERE owner_user_id=? AND kind=? AND input_hash=?", (user_id, kind, input_hash)
    ).fetchone()
    if existing:
        return existing, False
    job_id = uid()
    db.execute("""INSERT INTO jobs(id,owner_user_id,kind,status,source_id,session_id,input_hash,progress_total,
                                  result,created_at) VALUES (?,?,?,'queued',?,?,?,?,?,?)""",
               (job_id, user_id, kind, source_id, session_id, input_hash, total, dump(input_data), now()))
    return db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone(), True


def schedule_evaluation(db, user_id, sessions, method, purpose, *, source_id=None, parent=None, config_version=None, sample_id=None, request_key=None):
    config_version = config_version or active_config(db)
    snapshots = []
    for session in sessions:
        cutoff = db.execute("SELECT COALESCE(MAX(sequence_no),0) FROM events WHERE session_id=?",
                            (session["id"],)).fetchone()[0]
        snapshots.append((session["id"], cutoff))
    inputs = {"snapshots": snapshots, "method": method, "purpose": purpose,
              "config_version": config_version, "parent": parent}
    if sample_id:
        inputs["sample_id"] = sample_id
    if request_key:
        inputs["request_key"] = request_key
    job, created = enqueue(db, user_id, "evaluation", inputs, len(snapshots), source_id=source_id,
                           session_id=None if source_id or len(snapshots) != 1 else snapshots[0][0])
    if created:
        for session_id, cutoff in snapshots:
            db.execute("""INSERT INTO evaluation_runs(id,session_id,job_id,method,purpose,snapshot_hash,event_cutoff,
                          config_version,parent_run_id,status,created_at) VALUES (?,?,?,?,?,?,?,?,?,'queued',?)""",
                       (uid(), session_id, job["id"], method, purpose, digest(dump([session_id, cutoff])),
                        cutoff, config_version, parent, now()))
    return job


def public_job(db, job):
    response = {key: job[key] for key in ("id", "kind", "status", "progress_total", "progress_done",
                "succeeded_count", "failed_count", "skipped_count", "retry_count", "created_at")}
    response["error"] = json.loads(job["error"]) if job["error"] else None
    response["result"] = json.loads(job["result"]) if job["status"] in TERMINAL and job["result"] else None
    if job["kind"] == "evaluation":
        response["runs"] = [dict(run) for run in db.execute(
            """SELECT r.id,r.session_id,r.status,r.error,rv.id AS report_id,json_extract(rv.body,'$.provider') AS provider,
                      json_extract(rv.body,'$.degraded') AS degraded,
                      json_extract(rv.body,'$.process_score') AS process_score,
                      json_extract(rv.body,'$.process_score_five') AS process_score_five,
                      json_extract(rv.body,'$.process_coverage.scored_count') AS scored_dimensions,
                      q.actual_model_calls,q.provider_response_valid,
                      json_extract(rv.body,'$.provider_call.segments') AS segment_count,
                      p.stage,t.title AS task_title
               FROM evaluation_runs r LEFT JOIN report_versions rv ON rv.id=(
                 SELECT v.id FROM report_versions v WHERE v.run_id=r.id AND COALESCE(json_extract(v.body,'$.review.draft'),0)=0
                 AND NOT EXISTS (SELECT 1 FROM studychat_quality_rejections reject WHERE reject.report_id=v.id)
                 ORDER BY v.version DESC LIMIT 1)
               LEFT JOIN evaluation_progress p ON p.run_id=r.id LEFT JOIN studychat_run_quality q ON q.run_id=r.id
               JOIN sessions s ON s.id=r.session_id JOIN task_definitions t ON t.id=s.task_id
               WHERE r.job_id=? ORDER BY r.rowid""", (job["id"],)
        )]
        analysis = db.execute("SELECT model,provider_call_limit,provider_calls_started FROM analysis_batches WHERE job_id=?", (job["id"],)).fetchone()
        if analysis:
            response["analysis"] = dict(analysis)
        response["result"] = None
    return response


def claim(settings):
    with database(settings, write=True) as db:
        stamp = time.time()
        for job in db.execute(
            "SELECT * FROM jobs WHERE status IN ('running','cancel_requested') AND lease_expires<=?", (stamp,)
        ).fetchall():
            status = "cancelled" if job["status"] == "cancel_requested" else "queued" if job["retry_count"] < 2 else "failed"
            db.execute("UPDATE job_attempts SET status='lease_lost',finished_at=? WHERE lease_token=?",
                       (now(), job["lease_token"]))
            db.execute("""UPDATE jobs SET status=?, retry_count=retry_count+1, lease_token=NULL, lease_expires=NULL,
                       available_at=?, error=? WHERE id=?""",
                       (status, stamp + 2 ** job["retry_count"],
                        dump({"code": "WORKER_LEASE_LOST", "message": "后台任务租约已过期。"}) if status == "failed" else None,
                        job["id"]))
            if status in ("failed", "cancelled"):
                pending = db.execute("UPDATE evaluation_runs SET status=? WHERE job_id=? AND status='queued'",
                                     (status, job["id"])).rowcount
                if status == "failed":
                    failed = pending if job["kind"] == "evaluation" else job["progress_total"]
                    db.execute("""UPDATE jobs SET failed_count=failed_count+?,progress_done=progress_done+?,
                                  status=CASE WHEN succeeded_count>0 THEN 'partial' ELSE status END WHERE id=?""",
                               (failed, failed, job["id"]))
        job = db.execute(
            "SELECT * FROM jobs WHERE status='queued' AND available_at<=? ORDER BY created_at LIMIT 1", (stamp,)
        ).fetchone()
        if not job:
            return None
        token = uid()
        changed = db.execute("""UPDATE jobs SET status='running',lease_token=?,lease_expires=?
                             WHERE id=? AND status='queued'""", (token, stamp + LEASE_SECONDS, job["id"])).rowcount
        if not changed:
            return None
        db.execute("INSERT INTO job_attempts VALUES (?,?,?,'running',?,NULL)", (uid(), job["id"], token, now()))
        return job["id"], token


def leased(db, job_id, token):
    return db.execute(
        "SELECT * FROM jobs WHERE id=? AND lease_token=? AND lease_expires>? AND status IN ('running','cancel_requested')",
        (job_id, token, time.time()),
    ).fetchone()


def finish(db, job, status, *, result=None, error=None):
    completed = 1 if status == "completed" and job["kind"] != "evaluation" else job["succeeded_count"]
    failed = 1 if status == "failed" and job["kind"] != "evaluation" else job["failed_count"]
    db.execute("""UPDATE jobs SET status=?,result=?,error=?,lease_token=NULL,lease_expires=NULL,
                                progress_done=?,succeeded_count=?,failed_count=? WHERE id=?""",
               (status, dump(result) if result is not None else None, dump(error) if error else None,
                completed + failed + job["skipped_count"], completed, failed, job["id"]))
    db.execute("UPDATE job_attempts SET status=?,finished_at=? WHERE lease_token=?",
               (status, now(), job["lease_token"]))


def cancel_running(db, job):
    db.execute("UPDATE evaluation_runs SET status='cancelled' WHERE job_id=? AND status='queued'", (job["id"],))
    finish(db, job, "cancelled")


def record_analysis_stage(settings, job_id, token, run_id, stage, *, provider_call=False):
    with database(settings, write=True) as db:
        job = leased(db, job_id, token)
        if not job or job["status"] == "cancel_requested":
            raise Problem(409, "ANALYSIS_STOPPED", "现场分析任务已停止。")
        if provider_call:
            changed = db.execute("""UPDATE analysis_batches SET provider_calls_started=provider_calls_started+1
                WHERE job_id=? AND provider_calls_started<provider_call_limit""", (job_id,)).rowcount
            if not changed:
                raise Problem(409, "ANALYSIS_CALL_LIMIT", "已达到本次分析确认的模型调用上限。")
            db.execute("UPDATE studychat_run_quality SET actual_model_calls=actual_model_calls+1 WHERE run_id=?", (run_id,))
        db.execute("""INSERT INTO evaluation_progress VALUES (?,?,?) ON CONFLICT(run_id)
            DO UPDATE SET stage=excluded.stage,updated_at=excluded.updated_at""", (run_id, stage, now()))


def run_evaluation(settings, job_id, token):
    from .studychat_policy import consented, is_snapshot
    from .five_provider import PILOT_SCOPE
    from .providers import deployment_key
    from .rubrics import FIVE_DIMENSION_VERSIONS, is_five_dimension
    from .studychat_quality import session_quality

    while True:
        with database(settings, write=True) as db:
            job = leased(db, job_id, token)
            if not job:
                return
            if job["status"] == "cancel_requested":
                cancel_running(db, job)
                return
            run = db.execute(
                "SELECT * FROM evaluation_runs WHERE job_id=? AND status='queued' ORDER BY rowid LIMIT 1", (job_id,)
            ).fetchone()
            if not run:
                status = ("partial" if job["succeeded_count"] else "failed") if job["failed_count"] else "completed"
                finish(db, job, status)
                return
            db.execute("UPDATE jobs SET lease_expires=? WHERE id=?", (time.time() + LEASE_SECONDS, job_id))
            analysis = db.execute("SELECT model FROM analysis_batches WHERE job_id=?", (job_id,)).fetchone()
            if analysis:
                db.execute("INSERT OR REPLACE INTO evaluation_progress VALUES (?,?,?)", (run["id"], "dialogue", now()))
        report, error, relevance_rejection = None, None, None
        def progress(stage, *, provider_call=False):
            if analysis:
                record_analysis_stage(settings, job_id, token, run["id"], stage, provider_call=provider_call)
        try:
            with database(settings) as db:
                user = db.execute("SELECT * FROM users WHERE id=?", (job["owner_user_id"],)).fetchone()
                session = get_session(db, user, run["session_id"], "evaluate")
                task = db.execute("SELECT * FROM task_definitions WHERE id=?", (session["task_id"],)).fetchone()
                config = json.loads(db.execute("SELECT settings FROM evaluation_configs WHERE id=?",
                                               (run["config_version"],)).fetchone()["settings"])
                events = event_payloads(db, session["id"], run["event_cutoff"])
                source = db.execute("SELECT * FROM data_sources WHERE id=?",
                                    (session["source_id"],)).fetchone()
                active_provider = db.execute("SELECT body FROM provider_settings WHERE id='active'").fetchone()
                active = json.loads(active_provider["body"]) if active_provider else {}
                studychat = is_snapshot(source)
                five_dimensions = is_five_dimension(config["rubric_version"])
                formal_studychat = studychat and five_dimensions
                enabled = bool(source and active.get("enabled") and active.get("retention_confirmed"))
                external_allowed = enabled and (
                    (not five_dimensions and active.get("provider") == "deepseek"
                     and config.get("external_provider", {}).get("provider") == "deepseek"
                     and consented(db, source["id"])) if studychat else bool(source["allow_external_model"]))
                pilot = db.execute("SELECT * FROM studychat_sample_members WHERE job_id=? AND session_id=?",
                                   (job_id, session["id"])).fetchone()
                sample_size = db.execute("SELECT count(*) FROM studychat_sample_members WHERE job_id=?", (job_id,)).fetchone()[0]
                if sample_size or json.loads(job["result"] or "{}").get("sample_id"):
                    if not (pilot and (1 if analysis else 3) <= sample_size <= 5 and sample_size == job["progress_total"]
                            and studychat and source["id"] == job["source_id"] and user["role"] == "admin"
                            and run["method"] == "hybrid" and run["purpose"] == "research"
                            and five_dimensions and pilot["scope_version"] == PILOT_SCOPE
                            and active.get("provider") == "deepseek" and active.get("model") == pilot["model"]):
                        raise Problem(409, "SAMPLE_SCOPE", "试跑会话或模型与受限授权不匹配。")
                    config["external_provider"] = active | {"timeout": 90}
                    pilot = dict(pilot) | {"out_of_task_event_ids": [row[0] for row in db.execute(
                        "SELECT event_id FROM studychat_sample_evidence_review WHERE job_id=? AND session_id=?",
                        (job_id, session["id"]))]}
                    external_allowed = True
                else:
                    pilot = None
                if formal_studychat:
                    if not (analysis and analysis["model"] == "deepseek-flash" and pilot and enabled
                            and deployment_key("deepseek", settings)):
                        raise Problem(409, "STUDYCHAT_MODEL_REQUIRED", "正式 StudyChat 五维报告须使用启用且授权的 DeepSeek。")
                    quality = session_quality(db, session, task, events)
                    recorded = db.execute("SELECT body FROM studychat_run_quality WHERE run_id=?", (run["id"],)).fetchone()
                    if quality["eligibility_status"] != "eligible" or not recorded or json.loads(recorded["body"]) != quality:
                        raise Problem(422, "STUDYCHAT_QUALITY_CHANGED", "会话未通过资格检查，或范围已变化。")
                    pilot["quality"] = quality
                since = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
                recent = db.execute("SELECT snapshot FROM provider_calls WHERE created_at>=? ORDER BY rowid DESC LIMIT 3",
                                    (since,)).fetchall() if external_allowed else []
                circuit_open = len(recent) == 3 and all(
                    json.loads(item["snapshot"]).get("error", "").startswith("PROVIDER_") for item in recent
                )
                if pilot and circuit_open:
                    raise Problem(503, "PROVIDER_CIRCUIT", "外部模型暂时不可用，未保存试跑报告。")
                progress("evaluation")
                report = evaluate(run, session, task, events, config, external_allowed and not circuit_open, studychat, settings,
                                  **({"pilot": pilot} if pilot else {}), **({"progress": progress} if analysis else {}))
                if formal_studychat:
                    audit = report.get("provider_call") or {}
                    if report["provider"] != "deepseek" or not audit.get("provider_response_valid") or not audit.get("actual_model_calls"):
                        report = None
                        raise Problem(503, "STUDYCHAT_MODEL_NOT_VERIFIED", "未收到通过校验的 DeepSeek 返回，未生成正式报告。")
                if external_allowed and circuit_open and run["method"] != "rules":
                    report["warnings"].append("外部模型连续失败，暂时使用规则评价。")
        except Problem as exc:
            error = {"code": exc.code, "message": exc.message}
            if exc.code == "MODEL_TASK_MISMATCH":
                relevance_rejection = exc.details
        except Exception:
            LOGGER.exception("Evaluation failed for job %s", job_id)
            error = {"code": "EVALUATION_FAILED", "message": "评价未完成，可在任务详情重试。"}
        with database(settings, write=True) as db:
            job = leased(db, job_id, token)
            if not job:
                return
            if job["status"] == "cancel_requested":
                cancel_running(db, job)
                return
            if relevance_rejection:
                saved = db.execute("SELECT body FROM studychat_run_quality WHERE run_id=?", (run["id"],)).fetchone()
                if saved:
                    verdict = relevance_rejection["relevance"]
                    quality = json.loads(saved[0])
                    status = "off_task" if verdict == "irrelevant" else "uncertain"
                    quality.update(eligibility_status=status, task_relevance=verdict, relevance_reason=relevance_rejection["text"],
                                   relevance_evidence_event_ids=relevance_rejection["event_ids"], grade_link_valid=False,
                                   submission_link_valid=False, exclusion_reasons=[status])
                    db.execute("UPDATE studychat_run_quality SET body=? WHERE run_id=?", (dump(quality), run["id"]))
                    db.execute("""INSERT OR IGNORE INTO studychat_quality_rejections
                        SELECT id,?,? FROM report_versions WHERE session_id=? AND json_extract(body,'$.rubric_version') IN (?,?)""",
                        (dump([status]), now(), run["session_id"], *FIVE_DIMENSION_VERSIONS))
                    db.execute("""DELETE FROM effective_reports WHERE session_id=? AND report_id IN
                        (SELECT report_id FROM studychat_quality_rejections)""", (run["session_id"],))
                    db.execute("UPDATE exports SET expires_at=0 WHERE source_id=?", (job["source_id"],))
            if report:
                db.execute("SAVEPOINT save_evaluation")
                try:
                    if formal_studychat:
                        applied = db.execute("""UPDATE studychat_run_quality SET provider_response_valid=1,body=?
                            WHERE run_id=? AND actual_model_calls>0 AND actual_model_calls=?""",
                            (dump(report["studychat_quality"]), run["id"], report["provider_call"]["actual_model_calls"])).rowcount
                        if not applied:
                            raise Problem(422, "STUDYCHAT_MODEL_NOT_VERIFIED", "缺少实际调用审计。")
                    newer = db.execute(
                        """SELECT 1 FROM effective_reports er JOIN report_versions rv ON rv.id=er.report_id
                           JOIN evaluation_runs newer ON newer.id=rv.run_id
                           WHERE er.session_id=? AND er.method=? AND er.purpose=? AND newer.rowid >
                           (SELECT rowid FROM evaluation_runs WHERE id=?)""",
                        (run["session_id"], run["method"], run["purpose"], run["id"]),
                    ).fetchone()
                    store_report(db, run, report, effective=not newer,
                                 release=run["purpose"] in {"demo", "student_feedback"} and not report["conflicts"])
                    if report["provider_call"]:
                        db.execute("INSERT INTO provider_calls VALUES (?,?,?,?)",
                                   (uid(), run["id"], dump(report["provider_call"]), now()))
                except Exception:
                    db.execute("ROLLBACK TO save_evaluation")
                    LOGGER.exception("Report persistence failed for job %s", job_id)
                    error = {"code": "REPORT_FAILED", "message": "报告证据或数据写入校验未通过。"}
                finally:
                    db.execute("RELEASE save_evaluation")
            if error:
                db.execute("UPDATE evaluation_runs SET status='failed',error=? WHERE id=?", (dump(error), run["id"]))
                counter = "failed_count"
            else:
                db.execute("UPDATE evaluation_runs SET status=?,error=NULL WHERE id=?", (report["status"], run["id"]))
                counter = "succeeded_count"
            db.execute(f"UPDATE jobs SET progress_done=progress_done+1,{counter}={counter}+1 WHERE id=?", (job_id,))


def run_single(settings, job_id, token):
    try:
        with database(settings, write=True) as db:
            job = leased(db, job_id, token)
            if not job:
                return
            if job["status"] == "cancel_requested":
                cancel_running(db, job)
                return
            inputs = json.loads(job["result"])
            if job["kind"] == "import":
                batch = db.execute("SELECT * FROM import_batches WHERE id=? AND owner_user_id=?",
                                   (inputs["batch_id"], job["owner_user_id"])).fetchone()
                metadata, preview = json.loads(batch["metadata"]), json.loads(batch["preview"])
                if preview.get("snapshot"):
                    result = persist_snapshot_import(db, batch["owner_user_id"], metadata, preview, batch["id"])
                    db.execute("DELETE FROM import_batch_records WHERE batch_id=?", (batch["id"],))
                else:
                    result = persist_import(db, batch["owner_user_id"], batch["adapter"], metadata, json.loads(batch["records"]))
                db.execute("UPDATE import_batches SET records=NULL WHERE id=?", (batch["id"],))
                db.execute("UPDATE jobs SET source_id=? WHERE id=?", (result["source_id"], job_id))
            elif job["kind"] == "export":
                from .exports import execute_export

                result = execute_export(db, job, inputs)
            else:
                raise Problem(500, "JOB_KIND", "未知的后台任务类型。")
            if not leased(db, job_id, token):
                raise Problem(503, "WORKER_LEASE_LOST", "后台任务租约已过期。")
            finish(db, job, "completed", result=result)
    except Exception as exc:
        if not isinstance(exc, Problem):
            LOGGER.exception("Background job failed: %s", job_id)
        with database(settings, write=True) as db:
            job = leased(db, job_id, token)
            if job:
                error = {"code": exc.code, "message": exc.message} if isinstance(exc, Problem) else {
                    "code": "JOB_FAILED", "message": "后台任务执行失败。"
                }
                finish(db, job, "failed", result=json.loads(job["result"]), error=error)


def snapshot_preview(settings, job_id, token):
    from .builtin_studychat import load_builtin
    from .studychat_snapshot import load_snapshot

    with database(settings) as db:
        job = leased(db, job_id, token)
        if not job:
            return
        inputs = json.loads(job["result"])
    try:
        if inputs.get("builtin"):
            records, preview = load_builtin(settings, inputs["metadata"])
            if inputs["metadata"]["source_version"] != inputs["revision"]:
                raise Problem(409, "BUILTIN_VERSION_CHANGED", "内置数据包版本已更新，请重新发起导入。")
        else:
            records, preview = load_snapshot(settings, inputs["metadata"], inputs["order"])
        with database(settings, write=True) as db:
            job = leased(db, job_id, token)
            if not job:
                return
            if job["status"] == "cancel_requested":
                cancel_running(db, job)
                return
            metadata = inputs["metadata"]
            stamp = digest(dump(["studychat-local", metadata]))
            existing = db.execute("SELECT id FROM import_batches WHERE owner_user_id=? AND input_hash=?",
                                  (job["owner_user_id"], stamp)).fetchone()
            batch_id = existing["id"] if existing else uid()
            if not existing:
                db.execute("INSERT INTO import_batches VALUES (?,?,?,?,?,?,?,?)",
                           (batch_id, job["owner_user_id"], "studychat", dump(preview), None, dump(metadata), stamp, now()))
                db.executemany("INSERT INTO import_batch_records VALUES (?,?,?)",
                               ((batch_id, index, dump(record)) for index, record in enumerate(records)))
            if not leased(db, job_id, token):
                raise Problem(503, "WORKER_LEASE_LOST", "后台任务租约已过期。")
            finish(db, job, "completed", result={"batch_id": batch_id})
    except Exception as exc:
        if not isinstance(exc, Problem):
            LOGGER.exception("StudyChat preview failed for job %s", job_id)
        with database(settings, write=True) as db:
            job = leased(db, job_id, token)
            if not job:
                return
            error = {"code": exc.code, "message": exc.message, "details": exc.details} if isinstance(exc, Problem) else {
                "code": "JOB_FAILED", "message": "读取本地 StudyChat 快照失败。"
            }
            finish(db, job, "failed", result=json.loads(job["result"]), error=error)


def heartbeat(settings, job_id, token, stop):
    while not stop.wait(10):
        try:
            with database(settings, write=True) as db:
                if not leased(db, job_id, token):
                    return
                db.execute("UPDATE jobs SET lease_expires=? WHERE id=?", (time.time() + LEASE_SECONDS, job_id))
        except Exception:
            LOGGER.warning("Unable to renew job lease for %s", job_id)


def process_one(settings):
    dispatch = claim(settings)
    if not dispatch:
        return False
    job_id, token = dispatch
    with database(settings) as db:
        job = leased(db, job_id, token)
        kind = job["kind"] if job else None
    if kind:
        stop = threading.Event()
        thread = threading.Thread(target=heartbeat, args=(settings, job_id, token, stop), daemon=True)
        thread.start()
        try:
            if kind == "evaluation":
                run_evaluation(settings, job_id, token)
            elif kind == "studychat_preview":
                snapshot_preview(settings, job_id, token)
            else:
                run_single(settings, job_id, token)
        finally:
            stop.set()
            thread.join()
    return True


def consume_outbox(settings):
    with database(settings, write=True) as db:
        item = db.execute("SELECT * FROM outbox WHERE processed_at IS NULL ORDER BY rowid LIMIT 1").fetchone()
        if not item:
            return
        session = db.execute("SELECT * FROM sessions WHERE id=?", (item["session_id"],)).fetchone()
        metadata = json.loads(session["metadata"])
        schedule_evaluation(db, session["owner_user_id"], [session], "hybrid",
                            "demo" if metadata.get("fixture_id") else "student_feedback",
                            config_version=item["config_version"])
        db.execute("UPDATE outbox SET processed_at=? WHERE submission_id=?", (now(), item["submission_id"]))


def run_worker(settings):
    logging.basicConfig(level=logging.INFO)
    LOGGER.info("Evaluation worker ready")
    while True:
        consume_outbox(settings)
        if not process_one(settings):
            time.sleep(0.5)
