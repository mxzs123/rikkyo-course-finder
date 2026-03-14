import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://sy.rikkyo.ac.jp/web"
SEARCH_URL = f"{BASE_URL}/web_search_show.php"
DEFAULT_TIMEOUT = 30
SEARCH_PAGE_SIZE = 20

_thread_local = threading.local()

# In-memory caches
_eval_cache = {}
_eval_cache_lock = threading.Lock()
_search_cache = {}
_search_cache_lock = threading.Lock()

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Origin": "https://sy.rikkyo.ac.jp",
    "Referer": f"{BASE_URL}/web_search.php?&nendo=2025&t_mode=pc&gakubu=",
    "Content-Type": "application/x-www-form-urlencoded",
}

ICON_MAP = {
    "./image/ri_icon01.jpg": "科目コード登録",
    "./image/ri_icon02.jpg": "自動登録",
    "./image/ri_icon03.jpg": "抽選登録",
    "./image/ri_icon04.jpg": "抽選他",
    "./image/ri_icon05.jpg": "その他登録",
    "./image/ri_icon06.jpg": "備考参照",
}

GAKUBU_MAP = {
    "": "全て",
    "1": "文学部", "3": "経済学部", "4": "理学部", "5": "社会学部",
    "6": "法学部", "10": "経営学部", "12": "異文化コミュニケーション学部",
    "2": "GLAP", "37": "環境学部", "7": "観光学部", "8": "コミュニティ福祉学部",
    "11": "現代心理学部", "9": "スポーツウエルネス学部",
    "13": "全学共通科目・全学共通カリキュラム（総合系）",
    "14": "全学共通科目・全学共通カリキュラム（言語系）",
    "15": "学校・社会教育講座", "16": "日本語教育センター", "34": "新座学部共通科目",
    "17": "文学研究科", "18": "経済学研究科", "19": "理学研究科",
    "20": "社会学研究科", "21": "法学研究科", "28": "異文化コミュニケーション研究科",
    "30": "経営学研究科", "22": "観光学研究科", "23": "コミュニティ福祉学研究科",
    "31": "現代心理学研究科", "24": "スポーツウエルネス学研究科",
    "25": "ビジネスデザイン研究科", "26": "21世紀社会デザイン研究科",
    "27": "21世紀社会デザイン研究科（MSDA）", "35": "社会デザイン研究科",
    "36": "社会デザイン研究科（MSDA）", "32": "キリスト教学研究科",
    "33": "人工知能科学研究科", "29": "法務研究科",
}

GAKUBU_REVERSE = {v: k for k, v in GAKUBU_MAP.items() if k}

BUNRUI19_MAP = {
    "": "全て", "1": "大学", "2": "大学院（前期課程）",
    "3": "大学院（後期課程）", "4": "学校・社会教育講座",
}

BUNRUI19_REVERSE = {v: k for k, v in BUNRUI19_MAP.items() if k}

BUNRUI3_MAP = {
    "": "全て", "1": "対面（全回対面）", "2": "対面（一部オンライン）",
    "3": "オンライン（全回オンライン）", "4": "オンライン（一部対面）",
    "5": "オンデマンド（全回オンデマンド）", "7": "ハイフレックス", "6": "未定",
}

BUNRUI3_REVERSE = {v: k for k, v in BUNRUI3_MAP.items() if k}

BUNRUI12_MAP = {
    "": "全て", "1": "池袋", "2": "新座", "3": "他",
}

BUNRUI12_REVERSE = {v: k for k, v in BUNRUI12_MAP.items() if k}

BUNRUI2_MAP = {
    "": "全て", "1": "自動登録", "2": "科目コード登録", "3": "抽選登録",
    "6": "抽選他", "4": "その他登録", "7": "備考参照", "5": "未定",
}

BUNRUI2_REVERSE = {v: k for k, v in BUNRUI2_MAP.items() if k}

EXAM_KEYWORDS = ("試験", "テスト", "exam", "test", "quiz", "midterm", "final", "中間", "期末")
REPORT_KEYWORDS = ("レポート", "report", "essay", "paper")
WRITTEN_EXAM_KEYWORDS = ("筆記試験", "written exam")
IN_CLASS_KEYWORDS = ("平常点", "in-class", "attendance", "participation", "出席")


# ---------------------------------------------------------------------------
# Structured response wrappers (for AI / programmatic callers)
# ---------------------------------------------------------------------------

def _ok(data):
    return {"ok": True, "data": data}


def _err(code, message):
    return {"ok": False, "error": code, "message": message}


def _get_session():
    session = getattr(_thread_local, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update(HEADERS)
        _thread_local.session = session
    return session


def _request(method, url, **kwargs):
    session = _get_session()
    resp = session.request(method, url, timeout=DEFAULT_TIMEOUT, **kwargs)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    return resp


def _copy_course(course):
    copied = dict(course)
    evaluation = copied.get("evaluation")
    if isinstance(evaluation, dict):
        copied["evaluation"] = dict(evaluation)
    return copied


def _copy_result(result):
    return {
        "total": result["total"],
        "courses": [_copy_course(course) for course in result["courses"]],
        "max_page": result["max_page"],
    }


def _search_cache_key(page, kwargs):
    params = build_search_params(**kwargs)
    return page, tuple(sorted(params.items()))


def _jp_text(td):
    jp = td.find("span", class_="jp")
    if jp:
        return jp.get_text(strip=True)
    return td.get_text(strip=True)


def build_search_params(
    nendo="2025",
    gakubu="",
    kamokumei="",
    search_kamokumei="search_partial-match",
    bunrui19="",
    admin36_text="",
    search_admin36_text="search_partial-match",
    admin39_text="",
    search_admin39_text="search_partial-match",
    keyword_1="",
    keyword_2="",
    keyword_3="",
    kodo_2="",
    kodo_1="",
    bunrui3="",
    bunrui12="",
    bunrui2="",
):
    return {
        "nendo": nendo,
        "gakubu": gakubu,
        "kamokumei": kamokumei,
        "search_kamokumei": search_kamokumei,
        "admin31_text": "",
        "search_admin31_text": "search_partial-match",
        "bunrui19": bunrui19,
        "admin36_text": admin36_text,
        "search_admin36_text": search_admin36_text,
        "admin39_text": admin39_text,
        "search_admin39_text": search_admin39_text,
        "keyword_1": keyword_1,
        "keyword_2": keyword_2,
        "keyword_3": keyword_3,
        "kodo_2": kodo_2,
        "kodo_1": kodo_1,
        "bunrui3": bunrui3,
        "bunrui12": bunrui12,
        "bunrui2": bunrui2,
        "keyword": "key",
        "t_mode": "pc",
        "title_h2": "検索結果",
        "title_h2_eng": "Search results",
        "search": "show",
        "sortdir": "ASC",
        "sort": "admin26_80",
        "-find": " 検　索 ",
    }


def parse_results(html):
    soup = BeautifulSoup(html, "html.parser")

    title = soup.find("h2")
    total_match = re.search(r"（(\d+)件）", title.text) if title else None
    total = int(total_match.group(1)) if total_match else 0

    courses = []
    table = soup.find("table", class_="searchShow")
    if not table:
        return {"total": total, "courses": [], "max_page": 1}

    rows = table.find_all("tr")[1:]  # skip header
    for row in rows:
        tds = row.find_all("td")
        if len(tds) < 9:
            continue

        data_href = row.get("data-href", "")
        detail_url = f"{BASE_URL}/{data_href}" if data_href else ""

        code = _jp_text(tds[0])
        numbering = _jp_text(tds[1])
        name = _jp_text(tds[2])

        img = tds[3].find("img")
        reg_method = ""
        if img:
            src = img.get("src", "")
            reg_method = ICON_MAP.get(src, "")

        teacher = _jp_text(tds[4])
        semester = _jp_text(tds[5])
        schedule = _jp_text(tds[6])
        campus = _jp_text(tds[7])
        notes = _jp_text(tds[8])

        courses.append({
            "code": code,
            "numbering": numbering,
            "name": name,
            "detail_url": detail_url,
            "reg_method": reg_method,
            "teacher": teacher,
            "semester": semester,
            "schedule": schedule,
            "campus": campus,
            "notes": notes,
        })

    max_page = 1
    page_links = soup.select("ul.pagenav a")
    for link in page_links:
        href = link.get("href", "")
        m = re.search(r"page=(\d+)", href)
        if m:
            p = int(m.group(1))
            if p > max_page:
                max_page = p

    return {"total": total, "courses": courses, "max_page": max_page}


def search_courses(page=1, **kwargs):
    cache_key = _search_cache_key(page, kwargs)
    with _search_cache_lock:
        cached = _search_cache.get(cache_key)
    if cached is not None:
        return _copy_result(cached)

    params = build_search_params(**kwargs)

    if page == 1:
        resp = _request("POST", SEARCH_URL, data=params)
    else:
        query_params = dict(params)
        query_params["page"] = str(page)
        resp = _request("GET", SEARCH_URL, params=query_params)

    result = parse_results(resp.text)
    with _search_cache_lock:
        _search_cache[cache_key] = result
    return _copy_result(result)


def get_syllabus_detail(url=None, nendo=None, kodo_2=None):
    if nendo and kodo_2:
        url = f"{BASE_URL}/preview.php?nendo={nendo}&kodo_2={kodo_2}"
    if not url:
        return {}
    resp = _request("GET", url)
    soup = BeautifulSoup(resp.text, "html.parser")

    detail = {}

    attr_table = soup.find("table", class_="attribute")
    if attr_table:
        rows = attr_table.find_all("tr")
        for row in rows:
            tds = row.find_all("td")
            i = 0
            while i + 1 < len(tds):
                label_td = tds[i]
                value_td = tds[i + 1]
                jp_label = label_td.find("span", class_="jp")
                jp_value = value_td.find("span", class_="jp")
                label = jp_label.get_text(strip=True) if jp_label else label_td.get_text(strip=True)
                value = jp_value.get_text(strip=True) if jp_value else value_td.get_text(strip=True)
                label = label.split("/")[0].strip()
                if label:
                    detail[label] = value
                i += 2

    content_div = soup.find("div", class_="subjectContents")
    if content_div:
        current_heading = None
        sections = content_div.find_all(["h3", "p", "table"])
        for el in sections:
            if el.name == "h3":
                text = el.get_text(strip=True)
                m = re.match(r"【(.+?)】", text)
                current_heading = m.group(1) if m else text
            elif current_heading:
                if el.name == "table":
                    rows = el.find_all("tr")
                    headers = []
                    data_rows = []
                    for idx, row in enumerate(rows):
                        ths = row.find_all("th")
                        if idx == 0 and ths:
                            for th in ths:
                                jp = th.find("span", class_="jp")
                                headers.append(jp.get_text(strip=True) if jp else th.get_text(strip=True))
                        else:
                            tds = row.find_all(["td", "th"])
                            jp_texts = []
                            for td in tds:
                                jp = td.find("span", class_="jp")
                                jp_texts.append(jp.get_text(strip=True) if jp else td.get_text(strip=True))
                            if jp_texts:
                                data_rows.append(jp_texts)
                    table_data = {"type": "table", "headers": headers, "rows": data_rows}
                    # Attach extra text as note if section already has text
                    if current_heading in detail and isinstance(detail[current_heading], str):
                        table_data["note"] = detail[current_heading]
                    detail[current_heading] = table_data
                else:
                    jp = el.find("span", class_="jp")
                    text = jp.get_text("\n", strip=True) if jp else el.get_text("\n", strip=True)
                    if text:
                        existing = detail.get(current_heading)
                        if existing is None:
                            detail[current_heading] = text
                        elif isinstance(existing, str):
                            detail[current_heading] = existing + "\n" + text
                        elif isinstance(existing, dict) and existing.get("type") == "table":
                            existing.setdefault("note", "")
                            existing["note"] = (existing["note"] + "\n" + text).strip() if existing["note"] else text

    return detail


def _find_evaluation_table(content_div):
    found_heading = False
    for el in content_div.find_all(["h3", "table"]):
        if el.name == "h3":
            heading_text = el.get_text(" ", strip=True)
            if "成績評価方法" in heading_text:
                found_heading = True
            elif found_heading:
                break
        elif found_heading and el.name == "table":
            return el
    return None


def _contains_keyword(text, keywords):
    lowered = text.lower()
    return any(keyword in text or keyword in lowered for keyword in keywords)


def _is_exam_component(kind):
    return _contains_keyword(kind, EXAM_KEYWORDS)


def _is_report_component(kind):
    return _contains_keyword(kind, REPORT_KEYWORDS)


def _is_written_exam_component(kind):
    return _contains_keyword(kind, WRITTEN_EXAM_KEYWORDS)


def _is_in_class_component(kind):
    return _contains_keyword(kind, IN_CLASS_KEYWORDS)


def _parse_evaluation_info(html):
    soup = BeautifulSoup(html, "html.parser")
    content_div = soup.find("div", class_="subjectContents")
    if not content_div:
        return None

    target_table = _find_evaluation_table(content_div)
    if not target_table:
        return None

    exam_pct = 0
    written_exam_pct = 0
    report_pct = 0
    in_class_pct = 0
    other_pct = 0
    details_parts = []

    for row in target_table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 2:
            continue

        kind = _jp_text(cells[0])
        pct_text = _jp_text(cells[1])
        pct_match = re.search(r"(\d+)", pct_text)
        pct = int(pct_match.group(1)) if pct_match else 0
        criteria = _jp_text(cells[2]) if len(cells) > 2 else ""

        is_exam = _is_exam_component(kind)
        is_report = _is_report_component(kind)
        is_written_exam = _is_written_exam_component(kind)
        is_in_class = _is_in_class_component(kind)

        if is_exam:
            exam_pct += pct
        if is_written_exam:
            written_exam_pct += pct
        if is_report:
            report_pct += pct
        if is_in_class:
            in_class_pct += pct
        if not is_exam and not is_in_class:
            other_pct += pct

        if kind and pct_text:
            detail_line = f"{kind} {pct_text}"
            if criteria:
                detail_line += f" ({criteria})"
            details_parts.append(detail_line)

    return {
        "exam_pct": exam_pct,
        "written_exam_pct": written_exam_pct,
        "report_pct": report_pct,
        "in_class_pct": in_class_pct,
        "other_pct": other_pct,
        "has_exam": exam_pct > 0,
        "has_written_exam": written_exam_pct > 0,
        "has_report": report_pct > 0,
        "is_report_100": report_pct == 100,
        "details": "; ".join(details_parts),
    }


def _evaluation_cache_key(nendo, code):
    return f"{nendo}:{code}"


def _get_cached_evaluation(nendo, code):
    cache_key = _evaluation_cache_key(nendo, code)
    with _eval_cache_lock:
        cached = _eval_cache.get(cache_key)
    return dict(cached) if isinstance(cached, dict) else None


def _set_cached_evaluation(nendo, code, evaluation):
    if evaluation is None:
        return
    cache_key = _evaluation_cache_key(nendo, code)
    with _eval_cache_lock:
        _eval_cache[cache_key] = dict(evaluation)


def _fetch_evaluation(nendo, code):
    cached = _get_cached_evaluation(nendo, code)
    if cached is not None:
        return cached

    url = f"{BASE_URL}/preview.php?nendo={nendo}&kodo_2={code}"
    try:
        resp = _request("GET", url)
        evaluation = _parse_evaluation_info(resp.text)
        _set_cached_evaluation(nendo, code, evaluation)
        return dict(evaluation) if isinstance(evaluation, dict) else None
    except Exception:
        return None


def get_evaluation_batch(nendo, codes):
    results = {}

    missing_codes = []
    for code in codes:
        cached = _get_cached_evaluation(nendo, code)
        if cached is not None:
            results[code] = cached
        else:
            missing_codes.append(code)

    if not missing_codes:
        return results

    worker_count = min(12, len(missing_codes))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {executor.submit(_fetch_evaluation, nendo, code): code for code in missing_codes}
        for future in as_completed(futures):
            code = futures[future]
            try:
                eval_info = future.result()
                if eval_info is not None:
                    results[code] = eval_info
            except Exception:
                pass

    return results


def _matches_evaluation_filter(evaluation, exam_filter, exam_max, report_min=0):
    if not evaluation:
        return False

    exam_pct = evaluation.get("exam_pct", 0)
    has_exam = evaluation.get("has_exam", exam_pct > 0)
    has_report = evaluation.get("has_report", False)
    report_pct = evaluation.get("report_pct", 0)
    in_class_pct = evaluation.get("in_class_pct", 0)

    if exam_filter == "has-exam" and not has_exam:
        return False
    if exam_filter == "no-exam" and (has_exam or in_class_pct < 100):
        return False
    if exam_filter == "has-report" and not has_report:
        return False
    if exam_pct > exam_max:
        return False
    if report_pct < report_min:
        return False
    return True


def attach_evaluations_to_courses(courses, nendo):
    codes = [course["code"] for course in courses if course.get("code")]
    evaluations = get_evaluation_batch(nendo, codes)

    enriched_courses = []
    for index, course in enumerate(courses):
        enriched_course = _copy_course(course)
        evaluation = evaluations.get(course.get("code"))
        if evaluation is not None:
            enriched_course["evaluation"] = dict(evaluation)
        enriched_course["source_order"] = index
        enriched_courses.append(enriched_course)
    return enriched_courses


def filter_courses_by_evaluation(courses, exam_filter="all", exam_max=100, report_min=0):
    filtered_courses = []
    for course in courses:
        evaluation = course.get("evaluation")
        if _matches_evaluation_filter(evaluation, exam_filter, exam_max, report_min=report_min):
            filtered_courses.append(course)
    return filtered_courses


def search_courses_page_with_evaluations(page=1, exam_filter="all", exam_max=100, report_min=0, **kwargs):
    nendo = kwargs.get("nendo", "2025")
    page_result = search_courses(page=page, **kwargs)
    enriched_courses = attach_evaluations_to_courses(page_result["courses"], nendo)
    filtered_courses = filter_courses_by_evaluation(
        enriched_courses,
        exam_filter=exam_filter,
        exam_max=exam_max,
        report_min=report_min,
    )

    return {
        "page": page,
        "total": page_result["total"],
        "max_page": page_result["max_page"],
        "courses": filtered_courses,
    }


# ---------------------------------------------------------------------------
# Human-readable parameter resolution
# ---------------------------------------------------------------------------

def _resolve_with_reverse(value, reverse_map):
    if not value:
        return value
    if value in reverse_map:
        return reverse_map[value]
    matches = [k for k in reverse_map if value in k]
    if len(matches) == 1:
        return reverse_map[matches[0]]
    return value


def resolve_params(**kwargs):
    resolved = {}

    param_aliases = {
        "department": "gakubu",
        "course_name": "kamokumei",
        "teacher": "admin36_text",
        "category": "bunrui19",
        "format": "bunrui3",
        "campus": "bunrui12",
        "registration": "bunrui2",
        "year": "nendo",
        "course_code": "kodo_2",
        "numbering": "kodo_1",
    }

    reverse_lookups = {
        "gakubu": GAKUBU_REVERSE,
        "bunrui19": BUNRUI19_REVERSE,
        "bunrui3": BUNRUI3_REVERSE,
        "bunrui12": BUNRUI12_REVERSE,
        "bunrui2": BUNRUI2_REVERSE,
    }

    for key, value in kwargs.items():
        upstream_key = param_aliases.get(key, key)
        if upstream_key in reverse_lookups and isinstance(value, str):
            resolved[upstream_key] = _resolve_with_reverse(value, reverse_lookups[upstream_key])
        else:
            resolved[upstream_key] = value

    return resolved


def easy_search(page=1, **kwargs):
    params = resolve_params(**kwargs)
    return search_courses(page=page, **params)


def easy_search_with_evaluations(page=1, exam_filter="all", exam_max=100, report_min=0, **kwargs):
    params = resolve_params(**kwargs)
    return search_courses_page_with_evaluations(
        page=page, exam_filter=exam_filter, exam_max=exam_max, report_min=report_min, **params
    )


# ---------------------------------------------------------------------------
# Safe wrappers — structured responses for AI / programmatic callers
# ---------------------------------------------------------------------------

def safe_search(page=1, **kwargs):
    """Search with structured response wrapper."""
    try:
        result = search_courses(page=page, **kwargs)
        if result["total"] == 0:
            return _ok({"total": 0, "courses": [], "max_page": 1, "note": "no_results"})
        return _ok(result)
    except requests.exceptions.RequestException as e:
        return _err("network_error", str(e))
    except Exception as e:
        return _err("parse_error", str(e))


def safe_detail(url=None, nendo=None, kodo_2=None):
    """Get syllabus detail with structured response wrapper."""
    try:
        detail = get_syllabus_detail(url=url, nendo=nendo, kodo_2=kodo_2)
        if not detail:
            return _err("not_found", "Syllabus detail page returned no data")
        return _ok(detail)
    except requests.exceptions.RequestException as e:
        return _err("network_error", str(e))
    except Exception as e:
        return _err("parse_error", str(e))


def safe_search_with_evaluations(page=1, exam_filter="all", exam_max=100, report_min=0, **kwargs):
    """Search with evaluation filters and structured response wrapper."""
    try:
        result = search_courses_page_with_evaluations(
            page=page, exam_filter=exam_filter, exam_max=exam_max, report_min=report_min, **kwargs
        )
        if result["total"] == 0:
            return _ok({"total": 0, "courses": [], "max_page": 1, "page": page, "note": "no_results"})
        return _ok(result)
    except requests.exceptions.RequestException as e:
        return _err("network_error", str(e))
    except Exception as e:
        return _err("parse_error", str(e))
