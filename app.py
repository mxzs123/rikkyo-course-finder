from flask import Flask, render_template, request, jsonify
from scraper import (
    search_courses, search_courses_page_with_evaluations, get_syllabus_detail,
    GAKUBU_MAP, BUNRUI19_MAP, BUNRUI3_MAP, BUNRUI12_MAP, BUNRUI2_MAP,
)

app = Flask(__name__)


@app.after_request
def add_no_cache_headers(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.route("/")
def index():
    return render_template("index.html",
        gakubu_map=GAKUBU_MAP,
        bunrui19_map=BUNRUI19_MAP,
        bunrui3_map=BUNRUI3_MAP,
        bunrui12_map=BUNRUI12_MAP,
        bunrui2_map=BUNRUI2_MAP,
    )


@app.route("/api/search")
def api_search():
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1

    kwargs = {}

    field_map = {
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

    for param, kwarg in field_map.items():
        val = request.args.get(param)
        if val is not None:
            kwargs[kwarg] = val

    try:
        results = search_courses(page=page, **kwargs)
        return jsonify(results)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/search/evaluation-page")
def api_search_evaluation_page():
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1

    exam_filter = request.args.get("exam_filter", "all")
    if exam_filter not in {"all", "has-exam", "no-exam", "has-report"}:
        exam_filter = "all"

    try:
        exam_max = int(request.args.get("exam_max", 100))
    except ValueError:
        exam_max = 100
    exam_max = max(0, min(100, exam_max))

    try:
        report_min = int(request.args.get("report_min", 0))
    except ValueError:
        report_min = 0
    report_min = max(0, min(100, report_min))

    kwargs = {}
    field_map = {
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

    for param, kwarg in field_map.items():
        val = request.args.get(param)
        if val is not None:
            kwargs[kwarg] = val

    try:
        results = search_courses_page_with_evaluations(
            page=page,
            exam_filter=exam_filter,
            exam_max=exam_max,
            report_min=report_min,
            **kwargs,
        )
        return jsonify(results)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/detail")
def api_detail():
    nendo = request.args.get("nendo")
    kodo_2 = request.args.get("kodo_2")
    if not nendo or not kodo_2:
        return jsonify({"error": "nendo and kodo_2 parameters required"}), 400
    try:
        detail = get_syllabus_detail(nendo=nendo, kodo_2=kodo_2)
        return jsonify(detail)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5050))
    app.run(debug=True, host="0.0.0.0", port=port)
