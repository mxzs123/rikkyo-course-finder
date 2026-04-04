import os
import threading
import time
import uuid

import requests
import sentry_sdk
from flask import Flask, render_template, request, jsonify
from scraper import (
    search_courses, search_courses_page_with_evaluations, search_courses_all_pages_with_evaluations_parallel,
    get_structured_syllabus_detail,
    GAKUBU_MAP, BUNRUI19_MAP, BUNRUI3_MAP, BUNRUI12_MAP, BUNRUI2_MAP,
)
from sentry_sdk.integrations.flask import FlaskIntegration

app = Flask(__name__)

# Default 年度 for Web UI (override with env DEFAULT_NENDO e.g. on Railway)
DEFAULT_NENDO = os.environ.get("DEFAULT_NENDO", "2026")
EVALUATION_RUN_TTL_SECONDS = 15 * 60

SEARCH_FIELD_MAP = {
    "nendo": "nendo",
    "gakubu": "gakubu",
    "kamokumei": "kamokumei",
    "search_kamokumei": "search_kamokumei",
    "bunrui19": "bunrui19",
    "admin36_text": "admin36_text",
    "search_admin36_text": "search_admin36_text",
    "admin39_text": "admin39_text",
    "keyword_1": "keyword_1",
    "keyword_2": "keyword_2",
    "keyword_3": "keyword_3",
    "kodo_2": "kodo_2",
    "kodo_1": "kodo_1",
    "bunrui3": "bunrui3",
    "bunrui12": "bunrui12",
    "bunrui2": "bunrui2",
}

_evaluation_runs = {}
_evaluation_runs_lock = threading.Lock()


def _get_float_env(name, default):
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _init_sentry():
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        return

    sentry_sdk.init(
        dsn=dsn,
        integrations=[FlaskIntegration()],
        traces_sample_rate=_get_float_env("SENTRY_TRACES_SAMPLE_RATE", 0.0),
        environment=(
            os.environ.get("SENTRY_ENVIRONMENT")
            or os.environ.get("RAILWAY_ENVIRONMENT_NAME")
            or "production"
        ),
        release=os.environ.get("RAILWAY_GIT_COMMIT_SHA") or os.environ.get("SOURCE_VERSION"),
        send_default_pii=False,
    )


def _get_cloudflare_web_analytics_token():
    return os.environ.get("CLOUDFLARE_WEB_ANALYTICS_TOKEN", "").strip() or None


_init_sentry()


def _extract_search_kwargs(source):
    kwargs = {}
    for param, kwarg in SEARCH_FIELD_MAP.items():
        val = source.get(param)
        if val is not None:
            kwargs[kwarg] = val
    return kwargs


def _extract_evaluation_filters(source):
    exam_filter = source.get("exam_filter", "all")
    if exam_filter not in {"all", "has-exam", "no-exam", "has-report"}:
        exam_filter = "all"

    try:
        exam_max = int(source.get("exam_max", 100))
    except (TypeError, ValueError):
        exam_max = 100
    exam_max = max(0, min(100, exam_max))

    try:
        report_min = int(source.get("report_min", 0))
    except (TypeError, ValueError):
        report_min = 0
    report_min = max(0, min(100, report_min))

    return exam_filter, exam_max, report_min


def _cleanup_evaluation_runs():
    now = time.time()
    with _evaluation_runs_lock:
        expired_ids = [
            run_id
            for run_id, run in _evaluation_runs.items()
            if run.get("completed") and now - run.get("updated_at", now) > EVALUATION_RUN_TTL_SECONDS
        ]
        for run_id in expired_ids:
            _evaluation_runs.pop(run_id, None)


def _serialize_evaluation_run(run, known_count=0):
    courses = [dict(course) for course in run.get("aggregated_courses", [])]
    total_count = len(courses)

    try:
        known_count = max(0, int(known_count))
    except (TypeError, ValueError):
        known_count = 0

    if known_count > total_count:
        known_count = 0

    new_courses = courses[known_count:]
    return {
        "run_id": run["id"],
        "completed": run.get("completed", False),
        "base_total": run.get("base_total", 0),
        "pages_completed": run.get("pages_completed", 0),
        "max_page": run.get("max_page", 1),
        "aggregated_count": total_count,
        "new_courses": new_courses,
        "error": run.get("error"),
    }


def _run_evaluation_search(run_id, kwargs, exam_filter, exam_max, report_min):
    def handle_progress(event):
        with _evaluation_runs_lock:
            run = _evaluation_runs.get(run_id)
            if run is None:
                return

            if event["event"] == "start":
                run["base_total"] = event.get("total", 0)
                run["max_page"] = event.get("max_page", 1)
            elif event["event"] == "page":
                run["pages_completed"] = event.get("pages_completed", run["pages_completed"])
                run["max_page"] = event.get("max_page", run["max_page"])
                run["aggregated_courses"].extend(event.get("new_courses", []))
                run["aggregated_courses"].sort(key=lambda course: course.get("source_order", 0))
            elif event["event"] == "complete":
                run["pages_completed"] = event.get("pages_completed", run["pages_completed"])
                run["max_page"] = event.get("max_page", run["max_page"])
                run["base_total"] = event.get("total", run["base_total"])
                run["aggregated_courses"] = event.get("courses", run["aggregated_courses"])
                run["completed"] = True

            run["updated_at"] = time.time()

    try:
        result = search_courses_all_pages_with_evaluations_parallel(
            exam_filter=exam_filter,
            exam_max=exam_max,
            report_min=report_min,
            progress_callback=handle_progress,
            **kwargs,
        )
        with _evaluation_runs_lock:
            run = _evaluation_runs.get(run_id)
            if run is not None:
                run["base_total"] = result["total"]
                run["max_page"] = result["max_page"]
                run["pages_completed"] = result["pages_completed"]
                run["aggregated_courses"] = result["courses"]
                run["completed"] = True
                run["updated_at"] = time.time()
    except requests.exceptions.RequestException as e:
        sentry_sdk.capture_exception(e)
        error = str(e)
    except Exception as e:
        sentry_sdk.capture_exception(e)
        error = str(e)
    else:
        return

    with _evaluation_runs_lock:
        run = _evaluation_runs.get(run_id)
        if run is not None:
            run["completed"] = True
            run["error"] = error
            run["updated_at"] = time.time()


@app.after_request
def add_no_cache_headers(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.route("/")
def index():
    cloudflare_web_analytics_token = _get_cloudflare_web_analytics_token()
    return render_template("index.html",
        gakubu_map=GAKUBU_MAP,
        bunrui19_map=BUNRUI19_MAP,
        bunrui3_map=BUNRUI3_MAP,
        bunrui12_map=BUNRUI12_MAP,
        bunrui2_map=BUNRUI2_MAP,
        cloudflare_web_analytics_token=cloudflare_web_analytics_token,
        default_nendo=DEFAULT_NENDO,
    )


@app.route("/api/search")
def api_search():
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1

    kwargs = _extract_search_kwargs(request.args)

    try:
        results = search_courses(page=page, **kwargs)
        return jsonify(results)
    except requests.exceptions.RequestException as e:
        sentry_sdk.capture_exception(e)
        return jsonify({"error": str(e)}), 502
    except Exception as e:
        sentry_sdk.capture_exception(e)
        return jsonify({"error": str(e)}), 500


@app.route("/api/search/evaluation-page")
def api_search_evaluation_page():
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1

    exam_filter, exam_max, report_min = _extract_evaluation_filters(request.args)
    kwargs = _extract_search_kwargs(request.args)

    try:
        results = search_courses_page_with_evaluations(
            page=page,
            exam_filter=exam_filter,
            exam_max=exam_max,
            report_min=report_min,
            **kwargs,
        )
        return jsonify(results)
    except requests.exceptions.RequestException as e:
        sentry_sdk.capture_exception(e)
        return jsonify({"error": str(e)}), 502
    except Exception as e:
        sentry_sdk.capture_exception(e)
        return jsonify({"error": str(e)}), 500


@app.route("/api/search/evaluation-run", methods=["POST"])
def api_start_evaluation_run():
    payload = request.get_json(silent=True) or request.form or {}
    kwargs = _extract_search_kwargs(payload)
    exam_filter, exam_max, report_min = _extract_evaluation_filters(payload)

    run_id = uuid.uuid4().hex
    now = time.time()

    with _evaluation_runs_lock:
        _evaluation_runs[run_id] = {
            "id": run_id,
            "base_total": 0,
            "pages_completed": 0,
            "max_page": 1,
            "aggregated_courses": [],
            "completed": False,
            "error": None,
            "updated_at": now,
        }

    thread = threading.Thread(
        target=_run_evaluation_search,
        args=(run_id, kwargs, exam_filter, exam_max, report_min),
        daemon=True,
    )
    thread.start()
    _cleanup_evaluation_runs()
    return jsonify({"run_id": run_id})


@app.route("/api/search/evaluation-run/<run_id>")
def api_get_evaluation_run(run_id):
    _cleanup_evaluation_runs()
    known_count = request.args.get("known_count", 0)
    with _evaluation_runs_lock:
        run = _evaluation_runs.get(run_id)
        if run is None:
            return jsonify({"error": "run not found"}), 404
        payload = _serialize_evaluation_run(run, known_count=known_count)
    return jsonify(payload)


@app.route("/api/detail")
def api_detail():
    nendo = request.args.get("nendo")
    kodo_2 = request.args.get("kodo_2")
    if not nendo or not kodo_2:
        return jsonify({"error": "nendo and kodo_2 parameters required"}), 400
    try:
        detail = get_structured_syllabus_detail(nendo=nendo, kodo_2=kodo_2)
        return jsonify(detail)
    except requests.exceptions.RequestException as e:
        sentry_sdk.capture_exception(e)
        return jsonify({"error": str(e)}), 502
    except Exception as e:
        sentry_sdk.capture_exception(e)
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    app.run(debug=True, host="0.0.0.0", port=port)
