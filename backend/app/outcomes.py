import json

from .rubrics import is_five_dimension


def source_grade(metadata):
    return metadata.get("external_scores", {}).get("assignment_normalized")


def outcome_fields(system_score=None, source_score=None, *, studychat=False, local_review=False):
    percent = round(source_score * 100, 2) if source_score is not None else None
    use_local = local_review and system_score is not None
    use_source = studychat and source_score is not None and not use_local
    return {"source_outcome_score": source_score, "source_outcome_percent": percent,
            "system_outcome_score": system_score,
            "display_outcome_score": percent if use_source else system_score,
            "outcome_basis": "local_review" if use_local else "source_grade" if use_source else
            "system_rubric" if system_score is not None else None}


def diagnosis_for(process, outcome, config):
    process_ok = process >= config["process_threshold"] if process is not None else None
    outcome_ok = outcome >= config["outcome_threshold"] if outcome is not None else None
    label = {(True, True): "过程与成果均达标", (False, True): "过程需加强、成果达标",
             (True, False): "过程达标、成果需完善", (False, False): "过程与成果均需支持"}.get((process_ok, outcome_ok))
    def band(ok):
        return None if ok is None else "met" if ok else "needs_support"
    return {"label": label, "process_band": band(process_ok), "outcome_band": band(outcome_ok)}


def report_outcomes(db, report, metadata, studychat):
    from .studychat_quality import historical_model_valid

    quality = report.get("studychat_quality")
    grade_allowed = (quality["grade_link_valid"] if quality else
                     is_five_dimension(report["rubric_version"]) and historical_model_valid(report))
    fields = outcome_fields(report["outcome"]["score"], source_grade(metadata) if studychat and grade_allowed else None,
                            studychat=studychat, local_review=report["outcome"].get("basis") == "local_review")
    # Archived five-dimensional reports keep their bytes. Project the current
    # display diagnosis using their own frozen thresholds and archived grade.
    if is_five_dimension(report["rubric_version"]) and fields["outcome_basis"] == "source_grade" and "outcome_basis" not in report:
        config = db.execute("SELECT settings FROM evaluation_configs WHERE id=?", (report["config_version"],)).fetchone()
        if config:
            report = report | {"diagnosis": diagnosis_for(report["process_score"], fields["display_outcome_score"], json.loads(config[0])),
                               "outcome_display_projection": True}
    return report | fields
