"""Verify local running services with synthetic load and optionally the StudyChat snapshot."""
import argparse
import json
import math
import statistics
import time
from http.cookiejar import CookieJar
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPCookieProcessor, Request, build_opener


DONE = {"completed", "partial", "failed", "cancelled"}


class BrowserSession:
    def __init__(self, origin):
        self.origin = origin.rstrip("/")
        self.browser = build_opener(HTTPCookieProcessor(CookieJar()))
        self.csrf = ""
        self.csrf = self.request("/auth/demo", {"account": "teacher-demo"})["csrf"]

    def request(self, path, body=None, media_type="application/json"):
        headers = {"X-CSRF-Token": self.csrf} if body is not None else {}
        if body is not None:
            headers["Content-Type"] = media_type
            if not isinstance(body, bytes):
                body = json.dumps(body, ensure_ascii=False).encode()
        try:
            with self.browser.open(Request(self.origin + "/api" + path, body, headers), timeout=60) as response:
                return json.load(response)
        except HTTPError as error:
            problem = json.load(error)
            raise RuntimeError(f"{path}: HTTP {error.code}, {problem.get('code')}") from None

    def wait(self, item):
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            result = self.request("/jobs/" + item["id"])
            if result["status"] in DONE:
                if result["status"] != "completed":
                    raise RuntimeError(f"{result['kind']} ended in {result['status']}: {result.get('error')}")
                return result
            time.sleep(0.25)
        raise TimeoutError("worker did not finish job in 180 seconds")

    def commit(self, batch_id):
        response = self.request(f"/datasets/imports/{batch_id}/commit", {"license_confirmed": True})
        return self.wait(response)["result"]["source_id"]

    def evaluate(self, source, rows):
        batch = self.request("/evaluation-runs/batch", {
            "source_id": source, "method": "rules", "session_ids": [item["id"] for item in rows],
        })
        result = self.wait(batch)
        assert result["progress_done"] == len(rows) and result["failed_count"] == 0
        return result

    def sessions(self, source, **filters):
        return self.request("/sessions?" + urlencode({"source_id": source, "page_size": 100, **filters}))


def multipart(source):
    boundary = "assessment-local-check-boundary"
    data = []
    parts = {"adapter": "generic_json", "metadata": json.dumps({
        "name": "Synthetic engineering load", "source_identity": "acceptance-synthetic",
        "license_note": "Synthetic engineering fixture", "intended_use": "Local benchmark only",
        "retain_until": "2035-12-31",
    }), "file": json.dumps(source)}
    for name, value in parts.items():
        data += [f"--{boundary}", f'Content-Disposition: form-data; name="{name}"' +
                 ('; filename="check.json"' if name == "file" else ""), "", value]
    return ("\r\n".join([*data, f"--{boundary}--", ""]).encode(), "multipart/form-data; boundary=" + boundary)


def stats_ms(browser, path, check):
    for _ in range(2):
        check(browser.request(path))
    elapsed = []
    for _ in range(40):
        start = time.perf_counter()
        result = browser.request(path)
        elapsed.append((time.perf_counter() - start) * 1_000)
        check(result)
    return {"requests": len(elapsed), "p50_ms": round(statistics.median(elapsed), 2),
            "p95_ms": round(sorted(elapsed)[math.ceil(0.95 * len(elapsed)) - 1], 2)}


def synthetic(browser):
    rows = []
    for index in range(100):
        rows.append({"external_session_id": str(index), "participant_key": "synthetic-" + str(index),
                     "task": {"external_task_id": "load-test", "title": "Load test", "instructions": "Inspect recorded events."},
                     "events": [{"external_event_id": str(event), "sequence_no": event, "type": "editor_focus",
                                 "payload": {"text": ("synthetic data " * 15) + str(event)}}
                                for event in range(1, 2001 if index == 0 else 2)]})
    data, media = multipart({"schema_version": "1.0", "source": {"name": "Synthetic load"}, "sessions": rows})
    preview = browser.request("/datasets/imports", data, media)
    assert preview["valid"] == 100
    source = browser.commit(preview["id"])
    sessions = browser.sessions(source, sort="oldest")["items"]
    batch = browser.evaluate(source, sessions)
    sid = sessions[0]["id"]
    run_id = next(item["id"] for item in batch["runs"] if item["session_id"] == sid)

    def check_timeline(result):
        assert result["total"] == 2000 and len(result["items"]) == 20

    def check_report(result):
        assert result["session_id"] == sid and len(result["dimensions"]) == 4

    return {"batch_succeeded": batch["succeeded_count"], "batch_failed": batch["failed_count"],
            "timeline_page_50": stats_ms(browser, f"/sessions/{sid}/timeline?page=50&page_size=20", check_timeline),
            "report": stats_ms(browser, f"/evaluation-runs/{run_id}", check_report)}


def studychat(browser):
    started = time.perf_counter()
    job = browser.request("/datasets/imports/studychat-local", {
        "name": "StudyChat v1.1 本地验收", "semester": "all", "assignment": "all",
        "license_note": "CC BY 4.0. McNichols, Ikram and Lan. 本地开发验收。",
        "intended_use": "本地工程验证与许可范围内的研究复现", "retain_until": "2027-12-31",
    })
    batch_id = browser.wait(job)["result"]["batch_id"]
    preview = browser.request(f"/datasets/imports/{batch_id}/preview")
    preview_seconds = round(time.perf_counter() - started, 2)
    assert preview["valid"] == preview["count"] and not preview["snapshot"]["reports_run_log_present"]
    started = time.perf_counter()
    source_id = browser.commit(batch_id)
    sessions = browser.sessions(source_id, sort="oldest")
    assert sessions["total"] == preview["valid"]
    import_seconds = round(time.perf_counter() - started, 2)
    started = time.perf_counter()
    ids = [session["id"] for session in sessions["items"][:5]]
    quality = browser.request("/analysis/inspect", {"source_id": source_id, "session_ids": ids, "max_count": 5, "model": "rules"})
    assert quality["count"] == len(ids) and quality["expected_calls"] == 0 and all(row["quality"] for row in quality["sessions"])
    check_seconds = round(time.perf_counter() - started, 2)
    return {"preview_seconds": preview_seconds, "import_seconds": import_seconds, "check_seconds": check_seconds,
            "persisted_sessions": sessions["total"], "quality_checked": quality["count"],
            "eligible": quality["eligible_count"], "snapshot_counts": preview["snapshot"]["stats"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:5173")
    parser.add_argument("--studychat", action="store_true", help="Also verify the local StudyChat snapshot")
    options = parser.parse_args()
    if urlsplit(options.base_url).hostname not in {"127.0.0.1", "localhost"}:
        parser.error("Use a local loopback acceptance instance.")
    client = BrowserSession(options.base_url)
    result = {"synthetic": synthetic(client)}
    if options.studychat:
        result["studychat"] = studychat(client)
    print(json.dumps(result, ensure_ascii=False, indent=2))
