#!/usr/bin/env python3
"""MCP server for Rikkyo University course search (立教大学シラバス検索).

Run with: python3 mcp_server.py
Or: mcp run mcp_server.py
"""
import json

from mcp.server.fastmcp import FastMCP

from scraper import (
    easy_search,
    easy_search_with_evaluations,
    get_structured_syllabus_detail,
    search_and_detail_parallel,
    GAKUBU_MAP,
    BUNRUI19_MAP,
    BUNRUI3_MAP,
    BUNRUI12_MAP,
    BUNRUI2_MAP,
)

try:
    from scraper import natural_search, parse_natural_query
except ImportError:
    natural_search = None
    parse_natural_query = None

try:
    from scraper import compare_courses, check_schedule_conflicts, build_timetable
except ImportError:
    compare_courses = None
    check_schedule_conflicts = None
    build_timetable = None

mcp = FastMCP("rikkyo-xuanke")


def _json(obj):
    """Serialize an object to a pretty-printed JSON string."""
    return json.dumps(obj, ensure_ascii=False, indent=2)


@mcp.tool()
def search_courses(
    department: str = "",
    course_name: str = "",
    teacher: str = "",
    campus: str = "",
    category: str = "",
    format: str = "",
    registration: str = "",
    year: str = "2025",
    course_code: str = "",
    numbering: str = "",
    keyword_1: str = "",
    keyword_2: str = "",
    page: int = 1,
) -> str:
    """Search for courses at Rikkyo University (立教大学の科目を検索).

    Parameters:
        department: Department / 学部 (e.g. "文学部", "経済学部"). Use list_departments to see options.
        course_name: Course name / 科目名 (partial match).
        teacher: Instructor name / 教員名 (partial match).
        campus: Campus / キャンパス (e.g. "池袋", "新座").
        category: Category / 分類 (e.g. "大学", "大学院（前期課程）").
        format: Class format / 授業形態 (e.g. "対面（全回対面）", "オンライン（全回オンライン）").
        registration: Registration method / 登録方法 (e.g. "抽選登録", "科目コード登録").
        year: Academic year / 年度 (default "2025").
        course_code: Course code / 科目コード.
        numbering: Numbering code / ナンバリング.
        keyword_1: Keyword 1 / キーワード1.
        keyword_2: Keyword 2 / キーワード2.
        page: Page number (default 1, 20 results per page).

    Returns JSON with total count, courses list, and max_page.
    """
    try:
        result = easy_search(
            page=page,
            department=department,
            course_name=course_name,
            teacher=teacher,
            campus=campus,
            category=category,
            format=format,
            registration=registration,
            year=year,
            course_code=course_code,
            numbering=numbering,
            keyword_1=keyword_1,
            keyword_2=keyword_2,
        )
        return _json(result)
    except Exception as e:
        return _json({"ok": False, "error": "search_error", "message": str(e)})


@mcp.tool()
def search_with_evaluation(
    department: str = "",
    course_name: str = "",
    teacher: str = "",
    campus: str = "",
    category: str = "",
    format: str = "",
    registration: str = "",
    year: str = "2025",
    course_code: str = "",
    numbering: str = "",
    keyword_1: str = "",
    keyword_2: str = "",
    page: int = 1,
    exam_filter: str = "all",
    exam_max: int = 100,
    report_min: int = 0,
) -> str:
    """Search courses with evaluation/grading filters (成績評価フィルター付き検索).

    Same search parameters as search_courses, plus evaluation filters:

    Parameters:
        exam_filter: Filter by exam type. Options:
            "all" — no filter (default),
            "has-exam" — only courses with exams (試験あり),
            "no-exam" — only courses without exams (試験なし),
            "has-report" — only courses with reports (レポートあり).
        exam_max: Maximum exam percentage allowed (0-100, default 100).
        report_min: Minimum report percentage required (0-100, default 0).

    Returns JSON with courses enriched with evaluation breakdown (exam_pct, report_pct, etc.).
    """
    try:
        result = easy_search_with_evaluations(
            page=page,
            exam_filter=exam_filter,
            exam_max=exam_max,
            report_min=report_min,
            department=department,
            course_name=course_name,
            teacher=teacher,
            campus=campus,
            category=category,
            format=format,
            registration=registration,
            year=year,
            course_code=course_code,
            numbering=numbering,
            keyword_1=keyword_1,
            keyword_2=keyword_2,
        )
        return _json(result)
    except Exception as e:
        return _json({"ok": False, "error": "search_error", "message": str(e)})


@mcp.tool()
def get_detail(code: str, year: str = "2025") -> str:
    """Get full syllabus detail for a course (科目のシラバス詳細を取得).

    Parameters:
        code: Course code / 科目コード (required).
        year: Academic year / 年度 (default "2025").

    Returns JSON with stable top-level keys plus `detail_fields` / `raw_detail`.
    """
    try:
        detail = get_structured_syllabus_detail(nendo=year, kodo_2=code)
        if not detail:
            return _json({"ok": False, "error": "not_found", "message": "No syllabus data found for this course."})
        return _json({"ok": True, "data": detail})
    except Exception as e:
        return _json({"ok": False, "error": "detail_error", "message": str(e)})


@mcp.tool()
def search_and_get_details(
    department: str = "",
    course_name: str = "",
    teacher: str = "",
    campus: str = "",
    category: str = "",
    format: str = "",
    registration: str = "",
    year: str = "2025",
    course_code: str = "",
    numbering: str = "",
    keyword_1: str = "",
    keyword_2: str = "",
    top_n: int = 5,
) -> str:
    """Search courses and fetch full syllabus details for top results in parallel (検索+詳細一括取得).

    Combines search and detail retrieval in one call for efficiency.

    Parameters:
        top_n: Number of top results to fetch full details for (default 5).
        (other params same as search_courses)

    Returns JSON with search-style course rows enriched by structured detail fields.
    """
    try:
        result = search_and_detail_parallel(
            top_n=top_n,
            department=department,
            course_name=course_name,
            teacher=teacher,
            campus=campus,
            category=category,
            format=format,
            registration=registration,
            year=year,
            course_code=course_code,
            numbering=numbering,
            keyword_1=keyword_1,
            keyword_2=keyword_2,
        )
        return _json(result)
    except Exception as e:
        return _json({"ok": False, "error": "search_detail_error", "message": str(e)})


@mcp.tool()
def natural_language_search(query: str, page: int = 1) -> str:
    """Search courses using natural language (自然言語で科目を検索).

    Parameters:
        query: Natural language query in Japanese or English
               (e.g. "月曜2限の経済学部の授業", "psychology courses on Tuesday").
        page: Page number (default 1).

    Returns JSON search results.
    """
    if natural_search is None:
        return _json({
            "ok": False,
            "error": "not_implemented",
            "message": "natural_search is not yet implemented. Use search_courses with explicit parameters instead.",
        })
    try:
        result = natural_search(query, page)
        return _json(result)
    except Exception as e:
        return _json({"ok": False, "error": "natural_search_error", "message": str(e)})


@mcp.tool()
def compare(codes: str, year: str = "2025") -> str:
    """Compare multiple courses side by side (複数科目の比較).

    Parameters:
        codes: Comma-separated course codes (e.g. "AA001,AB002,AC003").
        year: Academic year / 年度 (default "2025").

    Returns JSON with comparison data for the specified courses.
    """
    if compare_courses is None:
        return _json({
            "ok": False,
            "error": "not_implemented",
            "message": "compare_courses is not yet implemented. Use get_detail for each course individually instead.",
        })
    try:
        code_list = [c.strip() for c in codes.split(",") if c.strip()]
        if not code_list:
            return _json({"ok": False, "error": "invalid_input", "message": "No course codes provided."})
        result = compare_courses(code_list, year)
        return _json(result)
    except Exception as e:
        return _json({"ok": False, "error": "compare_error", "message": str(e)})


@mcp.tool()
def check_conflicts(courses_json: str) -> str:
    """Check for schedule conflicts between courses (時間割の重複チェック).

    Parameters:
        courses_json: JSON array of course objects, each with:
            - code: Course code / 科目コード
            - name: Course name / 科目名
            - schedule: Schedule string / 曜日時限 (e.g. "月2")
            Example: '[{"code":"AA001","name":"経済学入門","schedule":"月2"},
                       {"code":"AB002","name":"統計学","schedule":"月2"}]'

    Returns JSON with conflict information.
    """
    if check_schedule_conflicts is None:
        return _json({
            "ok": False,
            "error": "not_implemented",
            "message": "check_schedule_conflicts is not yet implemented.",
        })
    try:
        course_list = json.loads(courses_json)
        if not isinstance(course_list, list):
            return _json({"ok": False, "error": "invalid_input", "message": "courses_json must be a JSON array."})
        result = check_schedule_conflicts(course_list)
        return _json(result)
    except json.JSONDecodeError as e:
        return _json({"ok": False, "error": "invalid_json", "message": f"Failed to parse courses_json: {e}"})
    except Exception as e:
        return _json({"ok": False, "error": "conflict_check_error", "message": str(e)})


@mcp.tool()
def list_departments() -> str:
    """List all available departments/faculties (学部・研究科一覧).

    Returns a formatted list of department codes and names that can be used
    with the 'department' parameter in search tools.
    """
    lines = []
    for code, name in GAKUBU_MAP.items():
        if code:
            lines.append(f"  {name}")
    return "Available departments (学部・研究科):\n" + "\n".join(lines)


@mcp.tool()
def list_options() -> str:
    """List all search filter options (検索フィルターの選択肢一覧).

    Returns all available options for department, category, format, campus,
    and registration method filters. Use these values with search tools.
    """
    sections = []

    sections.append("=== Department / 学部 (department) ===")
    for code, name in GAKUBU_MAP.items():
        if code:
            sections.append(f"  {name}")

    sections.append("\n=== Category / 分類 (category) ===")
    for code, name in BUNRUI19_MAP.items():
        if code:
            sections.append(f"  {name}")

    sections.append("\n=== Class Format / 授業形態 (format) ===")
    for code, name in BUNRUI3_MAP.items():
        if code:
            sections.append(f"  {name}")

    sections.append("\n=== Campus / キャンパス (campus) ===")
    for code, name in BUNRUI12_MAP.items():
        if code:
            sections.append(f"  {name}")

    sections.append("\n=== Registration Method / 登録方法 (registration) ===")
    for code, name in BUNRUI2_MAP.items():
        if code:
            sections.append(f"  {name}")

    return "\n".join(sections)


if __name__ == "__main__":
    mcp.run()
