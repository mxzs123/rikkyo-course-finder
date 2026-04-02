#!/usr/bin/env python3
"""CLI interface for Rikkyo University course search.

Usage:
    python3 cli.py search --department 文学部 --course-name 英語
    python3 cli.py search --campus 池袋 --teacher 田中 --page 2
    python3 cli.py detail --code AF182
    python3 cli.py search-detail --department 経済学部 --top 3
    python3 cli.py schema
    python3 cli.py list-options
"""
import argparse
import json
import sys
from datetime import datetime

from scraper import (
    resolve_params,
    safe_detail, safe_search_advanced,
    search_and_detail_parallel,
    natural_search,
    compare_courses, check_schedule_conflicts, build_timetable,
    GAKUBU_MAP, BUNRUI19_MAP, BUNRUI3_MAP, BUNRUI12_MAP, BUNRUI2_MAP,
)
from rguide import (
    load_curriculum_map, find_default_map, annotate_courses,
    build_curriculum_map,
)

DEFAULT_ACADEMIC_YEAR = str(datetime.now().year)
DEFAULT_MULTI_PAGE_TIMEOUT = 180


def _json_out(data):
    json.dump(data, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def _multi_page_scan_enabled(args):
    return args.all_pages or args.max_results is not None


def _resolve_search_timeout(args):
    if not _multi_page_scan_enabled(args):
        return None
    if args.timeout is None or args.timeout <= 0:
        return None
    return args.timeout


def _collect_search_kwargs(args, include_registration=True, include_course_code=True, include_numbering=True):
    kwargs = {}
    if getattr(args, "department", None):
        kwargs["department"] = args.department
    if getattr(args, "course_name", None):
        kwargs["course_name"] = args.course_name
    if getattr(args, "teacher", None):
        kwargs["teacher"] = args.teacher
    if getattr(args, "campus", None):
        kwargs["campus"] = args.campus
    if getattr(args, "category", None):
        kwargs["category"] = args.category
    if getattr(args, "format", None):
        kwargs["format"] = args.format
    if include_registration and getattr(args, "registration", None):
        kwargs["registration"] = args.registration
    if getattr(args, "year", None):
        kwargs["year"] = args.year
    if include_course_code and getattr(args, "course_code", None):
        kwargs["course_code"] = args.course_code
    if include_numbering and getattr(args, "numbering", None):
        kwargs["numbering"] = args.numbering
    if getattr(args, "keyword", None):
        for i, kw in enumerate(args.keyword[:3], 1):
            kwargs[f"keyword_{i}"] = kw
    return kwargs


def _emit_search_progress(event):
    kind = event.get("event")
    pages_planned = event.get("pages_planned", 1)
    elapsed = event.get("elapsed_seconds", 0.0)

    if kind == "start":
        if pages_planned <= 1:
            return
        timeout_seconds = event.get("timeout_seconds")
        timeout_text = "disabled" if timeout_seconds is None else f"{timeout_seconds}s"
        print(
            (
                f"[progress] scanning {pages_planned} pages from page {event['start_page']} "
                f"(total={event['total']}, timeout={timeout_text})"
            ),
            file=sys.stderr,
            flush=True,
        )
        return

    if kind == "page":
        if pages_planned <= 1:
            return
        print(
            (
                f"[progress] page {event['page_index']}/{pages_planned} "
                f"(source={event['page']}, matched+={event['matched_on_page']}, "
                f"matched={event['matched_total']}, elapsed={elapsed:.1f}s)"
            ),
            file=sys.stderr,
            flush=True,
        )
        return

    if kind == "timeout":
        page_label = event.get("page")
        print(
            (
                f"[progress] timeout after {elapsed:.1f}s "
                f"(next_page={page_label}, fetched={event['pages_fetched']}, "
                f"matched={event['matched_total']})"
            ),
            file=sys.stderr,
            flush=True,
        )
        return

    if kind == "complete" and pages_planned > 1:
        status = "complete" if event.get("complete_results") else f"stopped:{event.get('stopped_reason')}"
        print(
            (
                f"[progress] done after {elapsed:.1f}s "
                f"(pages={event['pages_fetched']}/{pages_planned}, matched={event['matched_total']}, "
                f"status={status})"
            ),
            file=sys.stderr,
            flush=True,
        )


def _load_rguide_map(args):
    """Load curriculum map if --rguide is specified or auto-detect."""
    rguide_path = getattr(args, "rguide", None)
    if rguide_path is None:
        return None
    if rguide_path == "auto":
        rguide_path = find_default_map()
        if not rguide_path:
            return None
    return load_curriculum_map(rguide_path)


def _apply_rguide(result, curriculum_map):
    """Annotate search result courses with rguide_category."""
    if not curriculum_map:
        return result
    if result.get("ok") and isinstance(result.get("data"), dict):
        courses = result["data"].get("courses", [])
        annotate_courses(courses, curriculum_map)
    return result


def cmd_search(args):
    kwargs = _collect_search_kwargs(args)
    curriculum_map = _load_rguide_map(args)

    result = safe_search_advanced(
        page=args.page,
        all_pages=args.all_pages,
        max_results=args.max_results,
        timeout_seconds=_resolve_search_timeout(args),
        progress_callback=_emit_search_progress if _multi_page_scan_enabled(args) else None,
        semester_filters=args.semester or [],
        curriculum_filters=args.curriculum or [],
        exam_filter=args.exam_filter,
        exam_max=args.exam_max,
        report_min=args.report_min,
        no_test=args.no_test,
        no_presentation=args.no_presentation,
        **resolve_params(**kwargs),
    )

    _apply_rguide(result, curriculum_map)
    _json_out(result)


def cmd_detail(args):
    result = safe_detail(nendo=args.year, kodo_2=args.code)
    _json_out(result)


def cmd_search_detail(args):
    kwargs = _collect_search_kwargs(args)
    curriculum_map = _load_rguide_map(args)

    result = search_and_detail_parallel(
        top_n=args.top,
        all_results=args.all_results,
        page=args.page,
        all_pages=args.all_pages,
        max_results=args.max_results,
        timeout_seconds=_resolve_search_timeout(args),
        progress_callback=_emit_search_progress if _multi_page_scan_enabled(args) else None,
        semester_filters=args.semester or [],
        curriculum_filters=args.curriculum or [],
        exam_filter=args.exam_filter,
        exam_max=args.exam_max,
        report_min=args.report_min,
        no_test=args.no_test,
        no_presentation=args.no_presentation,
        **kwargs,
    )
    _apply_rguide(result, curriculum_map)
    _json_out(result)


def cmd_schema(_args):
    schema = {
        "commands": {
            "search": {
                "description": "Search courses with filters. Returns paginated results.",
                "params": {
                    "department": {
                        "description": "学部名 (Department name in Japanese or numeric ID)",
                        "type": "string",
                        "server_side": True,
                        "values": {k: v for k, v in GAKUBU_MAP.items() if k},
                    },
                    "course_name": {
                        "description": "科目名 (Course name, partial match)",
                        "type": "string",
                        "server_side": True,
                    },
                    "teacher": {
                        "description": "教員名 (Teacher name, partial match)",
                        "type": "string",
                        "server_side": True,
                    },
                    "campus": {
                        "description": "校地 (Campus)",
                        "type": "string",
                        "server_side": True,
                        "values": {k: v for k, v in BUNRUI12_MAP.items() if k},
                    },
                    "category": {
                        "description": "科目設置区分 (Course category)",
                        "type": "string",
                        "server_side": True,
                        "values": {k: v for k, v in BUNRUI19_MAP.items() if k},
                    },
                    "format": {
                        "description": "授業形態 (Class format: in-person, online, etc.)",
                        "type": "string",
                        "server_side": True,
                        "values": {k: v for k, v in BUNRUI3_MAP.items() if k},
                    },
                    "registration": {
                        "description": "履修登録方法 (Registration method)",
                        "type": "string",
                        "server_side": True,
                        "values": {k: v for k, v in BUNRUI2_MAP.items() if k},
                    },
                    "year": {
                        "description": f"年度 (Academic year, default: current year: {DEFAULT_ACADEMIC_YEAR})",
                        "type": "string",
                        "default": DEFAULT_ACADEMIC_YEAR,
                        "server_side": True,
                    },
                    "course_code": {
                        "description": "科目コード (Course code, e.g. AF182)",
                        "type": "string",
                        "server_side": True,
                    },
                    "numbering": {
                        "description": "科目ナンバリング (Course numbering, e.g. EDU3700)",
                        "type": "string",
                        "server_side": True,
                    },
                    "keyword": {
                        "description": "シラバス内キーワード (Up to 3 keywords to search within syllabus)",
                        "type": "array",
                        "max_items": 3,
                        "server_side": True,
                    },
                    "page": {
                        "description": "Page number (20 results per page)",
                        "type": "integer",
                        "default": 1,
                        "server_side": True,
                    },
                    "all_pages": {
                        "description": "Fetch from the requested page through the final page; CLI-side pagination loop with stderr progress",
                        "type": "boolean",
                        "default": False,
                        "server_side": False,
                    },
                    "max_results": {
                        "description": "Maximum number of locally filtered results to return across pages",
                        "type": "integer",
                        "server_side": False,
                    },
                    "timeout": {
                        "description": f"Total timeout in seconds for CLI multi-page scans (default: {DEFAULT_MULTI_PAGE_TIMEOUT}, set 0 to disable)",
                        "type": "integer",
                        "default": DEFAULT_MULTI_PAGE_TIMEOUT,
                        "server_side": False,
                    },
                    "semester": {
                        "description": "学期フィルタ (e.g. 春学期, 秋学期, 通年). Applied client-side after fetch; use --all-pages for exhaustive results.",
                        "type": "array",
                        "server_side": False,
                    },
                    "curriculum": {
                        "description": "カリキュラム区分フィルタ (e.g. 学びの精神, 多彩な学び, 基幹BCD, 指定B1). Applied client-side after fetch; use --all-pages for exhaustive results.",
                        "type": "array",
                        "server_side": False,
                    },
                    "exam_filter": {
                        "description": "Filter by exam type, applied client-side after detail/evaluation enrichment",
                        "type": "string",
                        "values": ["all", "has-exam", "no-exam", "has-report"],
                        "default": "all",
                        "server_side": False,
                    },
                    "exam_max": {
                        "description": "Maximum exam percentage (0-100), applied client-side",
                        "type": "integer",
                        "default": 100,
                        "server_side": False,
                    },
                    "report_min": {
                        "description": "Minimum report percentage (0-100), applied client-side",
                        "type": "integer",
                        "default": 0,
                        "server_side": False,
                    },
                    "no_test": {
                        "description": "Exclude courses whose evaluation text contains any non-report test/quiz/exam; applied client-side",
                        "type": "boolean",
                        "default": False,
                        "server_side": False,
                    },
                    "no_presentation": {
                        "description": "Exclude courses whose evaluation text contains 発表 / プレゼン; applied client-side",
                        "type": "boolean",
                        "default": False,
                        "server_side": False,
                    },
                },
                "notes": [
                    "server_side=true means the parameter is sent to the upstream Rikkyo syllabus server.",
                    "server_side=false means the parameter is handled locally by this CLI after fetching pages.",
                    "Client-side filters only see fetched pages, so pair them with --all-pages when you need exhaustive matches.",
                ],
            },
            "detail": {
                "description": "Get full syllabus detail for a specific course.",
                "params": {
                    "code": {
                        "description": "科目コード (Course code)",
                        "type": "string",
                        "required": True,
                        "server_side": True,
                    },
                    "year": {
                        "description": "年度 (Academic year)",
                        "type": "string",
                        "default": DEFAULT_ACADEMIC_YEAR,
                        "server_side": True,
                    },
                },
            },
            "search-detail": {
                "description": "Search courses and fetch structured syllabus details in one call.",
                "params": {
                    "top": {
                        "description": "Number of matched results to fetch details for when --all-results is not set",
                        "type": "integer",
                        "default": 5,
                        "server_side": False,
                    },
                    "all_results": {
                        "description": "Fetch details for every matched result instead of only the top N",
                        "type": "boolean",
                        "default": False,
                        "server_side": False,
                    },
                },
                "note": "Accepts the same params as search plus 'top' / 'all_results'. Multi-page scan and local post-filters work here too.",
            },
            "schema": {
                "description": "Output this schema as JSON.",
                "params": {},
            },
            "list-options": {
                "description": "List all valid option values for dropdown/enum fields.",
                "params": {},
            },
        },
        "response_format": {
            "ok": "boolean - true if request succeeded",
            "data": "object - result data (when ok=true)",
            "error": "string - error code (when ok=false)",
            "message": "string - error description (when ok=false)",
        },
        "error_codes": {
            "invalid_params": "Input arguments are invalid",
            "network_error": "Upstream server unreachable or timed out",
            "parse_error": "HTML response could not be parsed",
            "no_results": "Search returned 0 results (noted in data, not an error)",
            "not_found": "Detail page not found or empty",
        },
    }
    _json_out(schema)


def cmd_nl_search(args):
    result = natural_search(args.query, page=args.page)
    _json_out(result)


def cmd_compare(args):
    codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    result = compare_courses(codes, nendo=args.year)
    _json_out(result)


def cmd_conflicts(args):
    courses = json.loads(args.courses)
    result = check_schedule_conflicts(courses)
    _json_out(result)


def cmd_timetable(args):
    courses = json.loads(args.courses)
    result = build_timetable(courses)
    _json_out(result)


def cmd_rguide_generate(args):
    import os
    cmap = build_curriculum_map(
        major_pdf=args.major,
        kikan_pdf=args.kikan,
        zenkari_pdf=args.zenkari,
        department=args.department,
        major=args.major_name,
        year=args.year,
    )
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(cmap, f, ensure_ascii=False, indent=2)
        f.write("\n")

    c2c = cmap["code_to_category"]
    cats = cmap["category_codes"]
    print(f"Generated {args.output}", file=sys.stderr)
    print(f"  Total codes mapped: {len(c2c)}", file=sys.stderr)
    for cat in sorted(cats.keys()):
        print(f"  {cat}: {len(cats[cat])} codes", file=sys.stderr)


def cmd_rguide_lookup(args):
    cmap = load_curriculum_map(args.map)
    from rguide import lookup_category
    results = {}
    for code in args.codes:
        cat = lookup_category(code, cmap)
        results[code] = cat or "(不明)"
    _json_out(results)


def cmd_list_options(_args):
    options = {
        "department (gakubu)": {v: k for k, v in GAKUBU_MAP.items() if k},
        "category (bunrui19)": {v: k for k, v in BUNRUI19_MAP.items() if k},
        "format (bunrui3)": {v: k for k, v in BUNRUI3_MAP.items() if k},
        "campus (bunrui12)": {v: k for k, v in BUNRUI12_MAP.items() if k},
        "registration (bunrui2)": {v: k for k, v in BUNRUI2_MAP.items() if k},
    }
    _json_out(options)


def main():
    parser = argparse.ArgumentParser(
        description="Rikkyo University course search CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # search
    sp_search = subparsers.add_parser("search", help="Search courses")
    sp_search.add_argument("--department", "-d", help="学部名")
    sp_search.add_argument("--course-name", "-n", help="科目名")
    sp_search.add_argument("--teacher", "-t", help="教員名")
    sp_search.add_argument("--campus", "-c", help="校地")
    sp_search.add_argument("--category", help="科目設置区分")
    sp_search.add_argument("--format", "-f", help="授業形態")
    sp_search.add_argument("--registration", help="履修登録方法")
    sp_search.add_argument("--year", "-y", default=DEFAULT_ACADEMIC_YEAR, help="年度")
    sp_search.add_argument("--course-code", help="科目コード")
    sp_search.add_argument("--numbering", help="科目ナンバリング")
    sp_search.add_argument("--keyword", "-k", action="append", help="キーワード (max 3)")
    sp_search.add_argument("--page", "-p", type=int, default=1, help="Page number")
    sp_search.add_argument("--all-pages", action="store_true", help="指定ページから最終ページまでまとめて取得（進捗はstderrに出力）")
    sp_search.add_argument("--max-results", type=int, help="返す最大件数（ページをまたいで収集）")
    sp_search.add_argument("--timeout", type=int, default=DEFAULT_MULTI_PAGE_TIMEOUT,
                           help=f"多ページ走査の総タイムアウト秒数（0で無効、既定: {DEFAULT_MULTI_PAGE_TIMEOUT}）")
    sp_search.add_argument("--semester", action="append",
                           help="学期で絞り込み（ローカル後段フィルタ。網羅検索は --all-pages 推奨）")
    sp_search.add_argument("--curriculum", action="append",
                           help="カリキュラム区分で絞り込み（ローカル後段フィルタ。網羅検索は --all-pages 推奨）")
    sp_search.add_argument("--exam-filter", default="all",
                           choices=["all", "has-exam", "no-exam", "has-report"])
    sp_search.add_argument("--exam-max", type=int, default=100, help="Max exam %% (0-100)")
    sp_search.add_argument("--report-min", type=int, default=0, help="Min report %% (0-100)")
    sp_search.add_argument("--no-test", action="store_true", help="テスト/小テスト/試験がある科目を除外（ローカル後段フィルタ）")
    sp_search.add_argument("--no-presentation", action="store_true", help="発表/プレゼンがある科目を除外（ローカル後段フィルタ）")
    sp_search.add_argument("--rguide", nargs="?", const="auto", default=None,
                           help="R Guideカリキュラムマップで区分を付与（パス指定 or 'auto'で自動検出）")
    sp_search.set_defaults(func=cmd_search)

    # detail
    sp_detail = subparsers.add_parser("detail", help="Get syllabus detail")
    sp_detail.add_argument("--code", required=True, help="科目コード")
    sp_detail.add_argument("--year", "-y", default=DEFAULT_ACADEMIC_YEAR, help="年度")
    sp_detail.set_defaults(func=cmd_detail)

    # search-detail
    sp_sd = subparsers.add_parser("search-detail", help="Search + fetch details")
    sp_sd.add_argument("--department", "-d", help="学部名")
    sp_sd.add_argument("--course-name", "-n", help="科目名")
    sp_sd.add_argument("--teacher", "-t", help="教員名")
    sp_sd.add_argument("--campus", "-c", help="校地")
    sp_sd.add_argument("--category", help="科目設置区分")
    sp_sd.add_argument("--format", "-f", help="授業形態")
    sp_sd.add_argument("--registration", help="履修登録方法")
    sp_sd.add_argument("--year", "-y", default=DEFAULT_ACADEMIC_YEAR, help="年度")
    sp_sd.add_argument("--course-code", help="科目コード")
    sp_sd.add_argument("--numbering", help="科目ナンバリング")
    sp_sd.add_argument("--keyword", "-k", action="append", help="キーワード")
    sp_sd.add_argument("--page", "-p", type=int, default=1, help="Page number")
    sp_sd.add_argument("--all-pages", action="store_true", help="指定ページから最終ページまでまとめて取得（進捗はstderrに出力）")
    sp_sd.add_argument("--max-results", type=int, help="返す最大件数（ページをまたいで収集）")
    sp_sd.add_argument("--timeout", type=int, default=DEFAULT_MULTI_PAGE_TIMEOUT,
                       help=f"多ページ走査の総タイムアウト秒数（0で無効、既定: {DEFAULT_MULTI_PAGE_TIMEOUT}）")
    sp_sd.add_argument("--semester", action="append",
                       help="学期で絞り込み（ローカル後段フィルタ。網羅検索は --all-pages 推奨）")
    sp_sd.add_argument("--curriculum", action="append",
                       help="カリキュラム区分で絞り込み（ローカル後段フィルタ。網羅検索は --all-pages 推奨）")
    sp_sd.add_argument("--exam-filter", default="all",
                       choices=["all", "has-exam", "no-exam", "has-report"])
    sp_sd.add_argument("--exam-max", type=int, default=100, help="Max exam %% (0-100)")
    sp_sd.add_argument("--report-min", type=int, default=0, help="Min report %% (0-100)")
    sp_sd.add_argument("--no-test", action="store_true", help="テスト/小テスト/試験がある科目を除外（ローカル後段フィルタ）")
    sp_sd.add_argument("--no-presentation", action="store_true", help="発表/プレゼンがある科目を除外（ローカル後段フィルタ）")
    sp_sd.add_argument("--top", type=int, default=5, help="Number of results to get details for when --all-results is not set")
    sp_sd.add_argument("--all-results", action="store_true", help="一致した結果すべてについて detail を取得")
    sp_sd.add_argument("--rguide", nargs="?", const="auto", default=None,
                       help="R Guideカリキュラムマップで区分を付与（パス指定 or 'auto'で自動検出）")
    sp_sd.set_defaults(func=cmd_search_detail)

    # nl-search
    sp_nl = subparsers.add_parser("nl-search", help="Search using natural language query")
    sp_nl.add_argument("query", help="Natural language query (e.g. '月曜2限の経済学部の英語')")
    sp_nl.add_argument("--page", "-p", type=int, default=1, help="Page number")
    sp_nl.set_defaults(func=cmd_nl_search)

    # compare
    sp_cmp = subparsers.add_parser("compare", help="Compare multiple courses")
    sp_cmp.add_argument("--codes", required=True, help="Comma-separated course codes")
    sp_cmp.add_argument("--year", "-y", default=DEFAULT_ACADEMIC_YEAR, help="年度")
    sp_cmp.set_defaults(func=cmd_compare)

    # conflicts
    sp_conf = subparsers.add_parser("conflicts", help="Check schedule conflicts")
    sp_conf.add_argument("--courses", required=True,
                         help='JSON array: [{"code":"A1","name":"Math","schedule":"月1"},...]')
    sp_conf.set_defaults(func=cmd_conflicts)

    # timetable
    sp_tt = subparsers.add_parser("timetable", help="Build timetable from courses")
    sp_tt.add_argument("--courses", required=True,
                       help='JSON array: [{"code":"A1","name":"Math","schedule":"月1"},...]')
    sp_tt.set_defaults(func=cmd_timetable)

    # schema
    sp_schema = subparsers.add_parser("schema", help="Output API schema as JSON")
    sp_schema.set_defaults(func=cmd_schema)

    # list-options
    sp_opts = subparsers.add_parser("list-options", help="List valid option values")
    sp_opts.set_defaults(func=cmd_list_options)

    # rguide
    sp_rg = subparsers.add_parser("rguide", help="Generate or query R Guide curriculum map")
    rg_sub = sp_rg.add_subparsers(dest="rguide_command", required=True)

    sp_rg_gen = rg_sub.add_parser("generate", help="Parse R Guide PDFs → curriculum map JSON")
    sp_rg_gen.add_argument("--major", help="専修別科目表 PDF (e.g. rguide_data/16_eibei.pdf)")
    sp_rg_gen.add_argument("--kikan", help="基幹科目 PDF (e.g. rguide_data/16_kikankamoku.pdf)")
    sp_rg_gen.add_argument("--zenkari", help="全学共通科目 PDF (e.g. rguide_data/kamokuhyo2016.pdf)")
    sp_rg_gen.add_argument("--department", default="文学部")
    sp_rg_gen.add_argument("--major-name", default="英米文学専修")
    sp_rg_gen.add_argument("--year", type=int, default=int(DEFAULT_ACADEMIC_YEAR))
    sp_rg_gen.add_argument("-o", "--output", default="rguide_data/curriculum_map.json")
    sp_rg_gen.set_defaults(func=cmd_rguide_generate)

    sp_rg_look = rg_sub.add_parser("lookup", help="Look up curriculum category for course codes")
    sp_rg_look.add_argument("--map", default="rguide_data/curriculum_map.json")
    sp_rg_look.add_argument("codes", nargs="+", help="Course codes")
    sp_rg_look.set_defaults(func=cmd_rguide_lookup)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
