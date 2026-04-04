import copy
import re
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

BASE_URL = "https://sy.rikkyo.ac.jp/web"
SEARCH_URL = f"{BASE_URL}/web_search_show.php"
DEFAULT_TIMEOUT = 30
SEARCH_PAGE_SIZE = 20
UPSTREAM_RETRY_COUNT = 3
UPSTREAM_RETRY_BACKOFF = 0.5
RETRYABLE_STATUS_CODES = (408, 429, 500, 502, 503, 504)
SEARCH_PAGE_FETCH_WORKERS = 4
EVALUATION_FETCH_WORKERS = 10

_thread_local = threading.local()

# In-memory caches
_eval_cache = {}
_eval_cache_lock = threading.Lock()
_search_cache = {}
_search_cache_lock = threading.Lock()
_detail_bundle_cache = {}
_detail_bundle_cache_lock = threading.Lock()

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Origin": "https://sy.rikkyo.ac.jp",
    "Referer": f"{BASE_URL}/web_search.php?&nendo=2026&t_mode=pc&gakubu=",
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
TEST_LIKE_KEYWORDS = (
    "小テスト", "テスト", "quiz", "midterm", "中間", "期末", "最終試験", "中間試験",
    "期末試験", "筆記試験", "口頭試問", "exam", "試験",
)
PRESENTATION_KEYWORDS = ("発表", "プレゼン", "presentation", "口頭発表")
REPORT_ONLY_PATTERNS = (
    r"レポート試験",
    r"report exam",
    r"final report",
    r"最終レポート",
)
CURRICULUM_LABEL_PATTERNS = (
    r"学びの精神(?:科目)?",
    r"多彩な学び(?:全学共通カリキュラム)?",
    r"主題別[A-ZＡ-Ｚ]",
    r"基幹[A-ZＡ-Ｚ0-9０-９]+",
    r"指定[A-ZＡ-Ｚ][0-9０-９]+",
)

DETAIL_FIELD_ALIASES = {
    "科目コード": "code",
    "科目ナンバリング": "numbering",
    "科目名": "name",
    "担当教員": "teacher",
    "教員名": "teacher",
    "学期": "semester",
    "曜日時限": "schedule",
    "曜日時限・教室": "schedule",
    "校地": "campus",
    "単位": "credits",
    "履修登録方法": "reg_method",
    "履修中止可否": "withdrawal_available",
    "授業形態": "format",
    "成績評価方法・基準": "evaluation_method",
    "注意事項": "notice",
    "授業の内容": "course_contents",
    "Course Contents": "course_contents",
    "授業の目標": "course_objectives",
    "Course Objectives": "course_objectives",
    "授業計画": "course_plan",
    "授業時間外（予習・復習等）の学修": "out_of_class_study",
    "テキスト": "textbook",
    "参考文献": "references",
}

STRUCTURED_DETAIL_SUMMARY_KEYS = (
    "code",
    "numbering",
    "name",
    "teacher",
    "semester",
    "schedule",
    "campus",
    "credits",
    "reg_method",
    "withdrawal_available",
    "format",
    "curriculum",
    "curriculum_text",
    "notice",
)


# ---------------------------------------------------------------------------
# Structured response wrappers (for AI / programmatic callers)
# ---------------------------------------------------------------------------

def _ok(data):
    return {"ok": True, "data": data}


def _err(code, message):
    return {"ok": False, "error": code, "message": message}


class UpstreamRequestError(requests.exceptions.RequestException):
    pass


def _get_session():
    session = getattr(_thread_local, "session", None)
    if session is None:
        retry = Retry(
            total=UPSTREAM_RETRY_COUNT,
            connect=UPSTREAM_RETRY_COUNT,
            read=UPSTREAM_RETRY_COUNT,
            status=UPSTREAM_RETRY_COUNT,
            backoff_factor=UPSTREAM_RETRY_BACKOFF,
            status_forcelist=RETRYABLE_STATUS_CODES,
            allowed_methods=None,
            raise_on_status=False,
            respect_retry_after_header=True,
        )
        adapter = HTTPAdapter(max_retries=retry)
        session = requests.Session()
        session.headers.update(HEADERS)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        _thread_local.session = session
    return session


def _is_retryable_request_exception(exc):
    if isinstance(exc, (
        requests.exceptions.ConnectionError,
        requests.exceptions.Timeout,
        requests.exceptions.ChunkedEncodingError,
        requests.exceptions.RetryError,
    )):
        return True

    if isinstance(exc, requests.exceptions.HTTPError) and exc.response is not None:
        return exc.response.status_code in RETRYABLE_STATUS_CODES

    return False


def _request(method, url, **kwargs):
    session = _get_session()
    try:
        resp = session.request(method, url, timeout=DEFAULT_TIMEOUT, **kwargs)
        resp.raise_for_status()
    except requests.exceptions.RequestException as exc:
        if _is_retryable_request_exception(exc):
            raise UpstreamRequestError(
                "上流シラバスサーバーへの接続に失敗しました。少し待ってから再試行してください。"
            ) from exc
        raise
    resp.encoding = "utf-8"
    return resp


def _copy_course(course):
    return copy.deepcopy(course)


def _copy_result(result):
    return {
        "total": result["total"],
        "courses": [_copy_course(course) for course in result["courses"]],
        "max_page": result["max_page"],
    }


def _copy_bundle(bundle):
    return copy.deepcopy(bundle)


def _search_cache_key(page, kwargs):
    params = build_search_params(**kwargs)
    return page, tuple(sorted(params.items()))


def _jp_text(td):
    jp = td.find("span", class_="jp")
    if jp:
        return jp.get_text(strip=True)
    return td.get_text(strip=True)


def build_search_params(
    nendo="2026",
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
        curriculum = _extract_curriculum_labels(notes)

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
            "curriculum": curriculum,
            "curriculum_text": _dedupe_lines(notes),
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


def _normalize_compact_text(text):
    return re.sub(r"\s+", "", (text or "")).replace("　", "").lower()


def _dedupe_lines(text):
    seen = set()
    deduped = []
    for line in (text or "").splitlines():
        cleaned = line.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        deduped.append(cleaned)
    return "\n".join(deduped)


def _is_empty_value(value):
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, (list, tuple, set)):
        return len(value) == 0
    if isinstance(value, dict) and value.get("type") == "table":
        return not value.get("rows") and not value.get("note")
    return False


def _contains_text(text, keyword):
    if not text or not keyword:
        return False
    lowered = text.lower()
    normalized_keyword = keyword.lower()
    compact_text = _normalize_compact_text(text)
    compact_keyword = _normalize_compact_text(keyword)
    return keyword in text or normalized_keyword in lowered or compact_keyword in compact_text


def _contains_keyword(text, keywords):
    return any(_contains_text(text, keyword) for keyword in keywords)


def _strip_report_only_phrases(text):
    cleaned = text or ""
    for pattern in REPORT_ONLY_PATTERNS:
        cleaned = re.sub(pattern, " ", cleaned, flags=re.IGNORECASE)
    return cleaned


def _contains_test_like_text(text):
    return _contains_keyword(_strip_report_only_phrases(text), TEST_LIKE_KEYWORDS)


def _contains_presentation_text(text):
    return _contains_keyword(text, PRESENTATION_KEYWORDS)


def _canonicalize_curriculum_label(label):
    if not label:
        return ""

    cleaned = re.sub(r"\s+", "", label).replace("　", "")
    cleaned = cleaned.strip("／/|・、,;；")
    cleaned = cleaned.replace("全学共通カリキュラム", "")
    cleaned = cleaned.replace("学びの精神科目", "学びの精神")

    if cleaned.startswith("多彩な学び"):
        return "多彩な学び"
    if cleaned.startswith("学びの精神"):
        return "学びの精神"
    return cleaned


def _normalize_detail_label(label):
    parts = [part.strip() for part in str(label or "").split("/") if part.strip()]
    if not parts:
        return ""
    return parts[0]


def _slugify_ascii(text):
    slug = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
    return slug


def _canonical_detail_key(raw_label, index=0):
    normalized_label = _normalize_detail_label(raw_label)
    if normalized_label in DETAIL_FIELD_ALIASES:
        return DETAIL_FIELD_ALIASES[normalized_label]
    if raw_label in DETAIL_FIELD_ALIASES:
        return DETAIL_FIELD_ALIASES[raw_label]

    parts = [part.strip() for part in str(raw_label or "").split("/") if part.strip()]
    for part in reversed(parts):
        if re.search(r"[A-Za-z]", part):
            slug = _slugify_ascii(part)
            if slug:
                return slug

    fallback = _slugify_ascii(normalized_label)
    if fallback:
        return fallback
    return f"field_{index + 1}"


def _extract_curriculum_labels(*texts):
    labels = []
    seen = set()

    for text in texts:
        if not text:
            continue

        raw_lines = [str(text)]
        raw_lines.extend(str(text).splitlines())
        for raw_line in raw_lines:
            if not raw_line:
                continue

            candidates = [raw_line]
            if "：" in raw_line:
                candidates.append(raw_line.split("：")[-1])
            if ":" in raw_line:
                candidates.append(raw_line.split(":")[-1])

            for candidate in candidates:
                for segment in re.split(r"[\n\r/／|]", candidate):
                    chunk = segment.strip()
                    if not chunk:
                        continue
                    for pattern in CURRICULUM_LABEL_PATTERNS:
                        for match in re.finditer(pattern, chunk):
                            label = _canonicalize_curriculum_label(match.group(0))
                            if label and label not in seen:
                                seen.add(label)
                                labels.append(label)

    return labels


def _stringify_detail_value(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and value.get("type") == "table":
        parts = []
        note = value.get("note")
        if note:
            parts.append(note)
        for row in value.get("rows", []):
            row_text = " ".join(cell for cell in row if cell)
            if row_text:
                parts.append(row_text)
        return "\n".join(parts)
    return ""


def _get_detail_text(detail, keyword):
    parts = []
    for key, value in detail.items():
        if keyword in key:
            text = _stringify_detail_value(value)
            if text:
                parts.append(text)
    return _dedupe_lines("\n".join(parts))


def _build_course_metadata(detail, evaluation=None):
    notice = _get_detail_text(detail, "注意事項")
    curriculum_labels = _extract_curriculum_labels(notice)

    return {
        "credits": _stringify_detail_value(detail.get("単位", "")),
        "semester": _stringify_detail_value(detail.get("学期", "")),
        "notice": notice,
        "curriculum": curriculum_labels,
        "curriculum_text": _dedupe_lines(notice),
        "has_test": bool(evaluation and evaluation.get("has_test")),
        "has_presentation": bool(evaluation and evaluation.get("has_presentation")),
    }


def _merge_course_metadata(base_metadata, search_notes=""):
    metadata = copy.deepcopy(base_metadata or {})
    combined_curriculum_text = _dedupe_lines(
        "\n".join(filter(None, [metadata.get("curriculum_text", ""), search_notes]))
    )
    metadata["curriculum_text"] = combined_curriculum_text

    labels = list(metadata.get("curriculum") or [])
    for label in _extract_curriculum_labels(search_notes):
        if label not in labels:
            labels.append(label)
    metadata["curriculum"] = labels
    return metadata


def _detail_bundle_cache_key(nendo, code):
    return f"{nendo}:{code}"


def _get_cached_detail_bundle(nendo, code):
    cache_key = _detail_bundle_cache_key(nendo, code)
    with _detail_bundle_cache_lock:
        cached = _detail_bundle_cache.get(cache_key)
    return _copy_bundle(cached) if isinstance(cached, dict) else None


def _set_cached_detail_bundle(nendo, code, bundle):
    if bundle is None:
        return
    cache_key = _detail_bundle_cache_key(nendo, code)
    with _detail_bundle_cache_lock:
        _detail_bundle_cache[cache_key] = _copy_bundle(bundle)


def parse_syllabus_detail_html(html):
    soup = BeautifulSoup(html, "html.parser")
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


def _build_detail_bundle_from_html(html):
    detail = parse_syllabus_detail_html(html)
    evaluation = _parse_evaluation_info(html)
    metadata = _build_course_metadata(detail, evaluation=evaluation)
    return {
        "detail": detail,
        "evaluation": evaluation,
        "metadata": metadata,
    }


def _merge_structured_field_value(existing, incoming):
    if _is_empty_value(existing):
        return copy.deepcopy(incoming)
    if _is_empty_value(incoming):
        return copy.deepcopy(existing)
    if existing == incoming:
        return copy.deepcopy(existing)
    if isinstance(existing, str) and isinstance(incoming, str):
        return _dedupe_lines("\n".join([existing, incoming]))
    return copy.deepcopy(existing)


def _build_structured_detail_fields(detail, metadata=None):
    detail_fields = {}
    detail_field_labels = {}

    for index, (raw_key, value) in enumerate((detail or {}).items()):
        canonical_key = _canonical_detail_key(raw_key, index=index)
        detail_fields[canonical_key] = _merge_structured_field_value(
            detail_fields.get(canonical_key),
            value,
        )
        detail_field_labels.setdefault(canonical_key, _normalize_detail_label(raw_key) or raw_key)

    metadata = metadata or {}
    curriculum = list(metadata.get("curriculum") or [])
    if curriculum:
        detail_fields["curriculum"] = curriculum
        detail_field_labels.setdefault("curriculum", "カリキュラム区分")

    curriculum_text = metadata.get("curriculum_text", "")
    if curriculum_text:
        detail_fields["curriculum_text"] = curriculum_text
        detail_field_labels.setdefault("curriculum_text", "カリキュラム区分メモ")

    return detail_fields, detail_field_labels


def _build_structured_syllabus_detail(bundle, nendo=None, kodo_2=None):
    bundle = bundle or {}
    raw_detail = copy.deepcopy(bundle.get("detail") or {})
    evaluation = copy.deepcopy(bundle.get("evaluation") or {})
    metadata = copy.deepcopy(bundle.get("metadata") or {})
    detail_fields, detail_field_labels = _build_structured_detail_fields(raw_detail, metadata=metadata)

    def field_text(key, default=""):
        return _stringify_detail_value(detail_fields.get(key, default))

    curriculum = list(detail_fields.get("curriculum") or metadata.get("curriculum") or [])
    curriculum_text = field_text("curriculum_text") or metadata.get("curriculum_text", "")
    notice = field_text("notice") or metadata.get("notice", "")

    structured = {
        "nendo": nendo,
        "code": field_text("code") or (kodo_2 or ""),
        "numbering": field_text("numbering"),
        "name": field_text("name"),
        "teacher": field_text("teacher"),
        "semester": field_text("semester") or metadata.get("semester", ""),
        "schedule": field_text("schedule"),
        "campus": field_text("campus"),
        "credits": field_text("credits") or metadata.get("credits", ""),
        "reg_method": field_text("reg_method"),
        "withdrawal_available": field_text("withdrawal_available"),
        "format": field_text("format"),
        "curriculum": curriculum,
        "curriculum_text": curriculum_text,
        "notice": notice,
        "evaluation": evaluation,
        "detail_fields": detail_fields,
        "detail_field_labels": detail_field_labels,
        "raw_detail": raw_detail,
    }

    if evaluation and "evaluation_method" not in structured["detail_fields"]:
        structured["detail_fields"]["evaluation_method"] = {
            "type": "table",
            "headers": ["指標", "値"],
            "rows": [
                ["試験", str(evaluation.get("exam_pct", 0))],
                ["筆記試験", str(evaluation.get("written_pct", 0))],
                ["レポート", str(evaluation.get("report_pct", 0))],
                ["平常点", str(evaluation.get("in_class_pct", 0))],
            ],
            "note": evaluation.get("notes", ""),
        }
        structured["detail_field_labels"].setdefault("evaluation_method", "成績評価方法・基準")

    return structured


def _fetch_detail_bundle(nendo, code):
    cached = _get_cached_detail_bundle(nendo, code)
    if cached is not None:
        return cached

    url = f"{BASE_URL}/preview.php?nendo={nendo}&kodo_2={code}"
    resp = _request("GET", url)
    bundle = _build_detail_bundle_from_html(resp.text)
    _set_cached_detail_bundle(nendo, code, bundle)
    if isinstance(bundle.get("evaluation"), dict):
        _set_cached_evaluation(nendo, code, bundle["evaluation"])
    return _copy_bundle(bundle)


def get_syllabus_detail(url=None, nendo=None, kodo_2=None):
    if nendo and kodo_2:
        bundle = _fetch_detail_bundle(nendo, kodo_2)
        return copy.deepcopy(bundle.get("detail") or {})
    if not url:
        return {}
    resp = _request("GET", url)
    return parse_syllabus_detail_html(resp.text)


def get_structured_syllabus_detail(url=None, nendo=None, kodo_2=None):
    if nendo and kodo_2:
        bundle = _fetch_detail_bundle(nendo, kodo_2)
        return _build_structured_syllabus_detail(bundle, nendo=nendo, kodo_2=kodo_2)
    if not url:
        return {}
    resp = _request("GET", url)
    bundle = _build_detail_bundle_from_html(resp.text)
    return _build_structured_syllabus_detail(bundle, nendo=nendo, kodo_2=kodo_2)


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
    notes_parts = []
    combined_text_parts = []
    components = []

    for row in target_table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) >= 2:
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

            detail_line = " ".join(part for part in [kind, pct_text] if part)
            if criteria:
                detail_line = f"{detail_line} ({criteria})" if detail_line else criteria
            if detail_line:
                details_parts.append(detail_line)
                combined_text_parts.append(detail_line)

            components.append({
                "kind": kind,
                "percentage": pct,
                "percentage_text": pct_text,
                "criteria": criteria,
                "has_test": _contains_test_like_text(" ".join(filter(None, [kind, criteria]))),
                "has_presentation": _contains_presentation_text(" ".join(filter(None, [kind, criteria]))),
            })
            continue

        note_text = _jp_text(row)
        if note_text and "備考" not in note_text:
            notes_parts.append(note_text)
            combined_text_parts.append(note_text)

    combined_text = "\n".join(filter(None, combined_text_parts))
    notes_text = _dedupe_lines("\n".join(notes_parts))

    return {
        "exam_pct": exam_pct,
        "written_pct": written_exam_pct,
        "written_exam_pct": written_exam_pct,
        "report_pct": report_pct,
        "in_class_pct": in_class_pct,
        "other_pct": other_pct,
        "has_exam": exam_pct > 0,
        "has_written_exam": written_exam_pct > 0,
        "has_report": report_pct > 0,
        "is_report_100": report_pct == 100,
        "has_test": _contains_test_like_text(combined_text),
        "has_presentation": _contains_presentation_text(combined_text),
        "notes": notes_text,
        "components": components,
        "details": "; ".join(details_parts),
        "combined_text": combined_text,
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


def _get_cached_evaluation_from_bundle(nendo, code):
    bundle = _get_cached_detail_bundle(nendo, code)
    if not isinstance(bundle, dict):
        return None

    evaluation = bundle.get("evaluation")
    if not isinstance(evaluation, dict):
        return None

    _set_cached_evaluation(nendo, code, evaluation)
    return dict(evaluation)


def _fetch_evaluation(nendo, code):
    cached = _get_cached_evaluation(nendo, code)
    if cached is not None:
        return cached

    cached_from_bundle = _get_cached_evaluation_from_bundle(nendo, code)
    if cached_from_bundle is not None:
        return cached_from_bundle

    try:
        url = f"{BASE_URL}/preview.php?nendo={nendo}&kodo_2={code}"
        resp = _request("GET", url)
        evaluation = _parse_evaluation_info(resp.text)
        if isinstance(evaluation, dict):
            _set_cached_evaluation(nendo, code, evaluation)
        return dict(evaluation) if isinstance(evaluation, dict) else None
    except Exception:
        return None


def get_course_bundle_batch(nendo, codes):
    results = {}
    missing_codes = []

    for code in codes:
        cached = _get_cached_detail_bundle(nendo, code)
        if cached is not None:
            results[code] = cached
        else:
            missing_codes.append(code)

    if not missing_codes:
        return results

    worker_count = min(12, len(missing_codes))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {executor.submit(_fetch_detail_bundle, nendo, code): code for code in missing_codes}
        for future in as_completed(futures):
            code = futures[future]
            try:
                bundle = future.result()
                if isinstance(bundle, dict):
                    results[code] = bundle
            except Exception:
                pass

    return results


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


def attach_evaluation_only_to_courses(courses, nendo):
    codes = [course["code"] for course in courses if course.get("code")]
    evaluations = get_evaluation_batch(nendo, codes)

    enriched_courses = []
    for index, course in enumerate(courses):
        enriched_course = _copy_course(course)
        evaluation = evaluations.get(course.get("code"))
        if evaluation is not None:
            enriched_course["evaluation"] = dict(evaluation)
            enriched_course["has_test"] = bool(evaluation.get("has_test"))
            enriched_course["has_presentation"] = bool(evaluation.get("has_presentation"))
        enriched_course["source_order"] = index
        enriched_courses.append(enriched_course)
    return enriched_courses


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
    bundles = get_course_bundle_batch(nendo, codes)

    enriched_courses = []
    for index, course in enumerate(courses):
        enriched_course = _copy_course(course)
        bundle = bundles.get(course.get("code")) or {}
        evaluation = bundle.get("evaluation")
        if evaluation is not None:
            enriched_course["evaluation"] = dict(evaluation)
            enriched_course["has_test"] = bool(evaluation.get("has_test"))
            enriched_course["has_presentation"] = bool(evaluation.get("has_presentation"))

        metadata = _merge_course_metadata(bundle.get("metadata"), enriched_course.get("notes", ""))
        if metadata:
            enriched_course["metadata"] = metadata
            if metadata.get("credits"):
                enriched_course["credits"] = metadata["credits"]
            if metadata.get("curriculum"):
                enriched_course["curriculum"] = list(metadata["curriculum"])
            if metadata.get("curriculum_text"):
                enriched_course["curriculum_text"] = metadata["curriculum_text"]
            if metadata.get("notice"):
                enriched_course["notice"] = metadata["notice"]
            if not enriched_course.get("semester") and metadata.get("semester"):
                enriched_course["semester"] = metadata["semester"]
            if "has_test" not in enriched_course:
                enriched_course["has_test"] = bool(metadata.get("has_test"))
            if "has_presentation" not in enriched_course:
                enriched_course["has_presentation"] = bool(metadata.get("has_presentation"))

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


def _matches_semester_filter(course, semester_filters=None):
    if not semester_filters:
        return True

    semester_text = course.get("semester") or (course.get("metadata") or {}).get("semester", "")
    normalized_semester = _normalize_compact_text(semester_text)
    return any(_normalize_compact_text(semester) in normalized_semester for semester in semester_filters)


def _matches_curriculum_filter(course, curriculum_filters=None):
    if not curriculum_filters:
        return True

    metadata = course.get("metadata") or {}
    labels = metadata.get("curriculum") or course.get("curriculum") or []
    search_space = "\n".join(filter(None, [metadata.get("curriculum_text", ""), course.get("notes", "")]))
    normalized_search_space = _normalize_compact_text(search_space)

    for curriculum in curriculum_filters:
        normalized_curriculum = _normalize_compact_text(curriculum)
        if any(normalized_curriculum in _normalize_compact_text(label) for label in labels):
            return True
        if normalized_curriculum in normalized_search_space:
            return True

    return False


def _matches_course_filters(
    course,
    semester_filters=None,
    curriculum_filters=None,
    exam_filter="all",
    exam_max=100,
    report_min=0,
    no_test=False,
    no_presentation=False,
):
    if not _matches_semester_filter(course, semester_filters=semester_filters):
        return False
    if not _matches_curriculum_filter(course, curriculum_filters=curriculum_filters):
        return False

    if exam_filter != "all" or exam_max < 100 or report_min > 0:
        if not _matches_evaluation_filter(course.get("evaluation"), exam_filter, exam_max, report_min=report_min):
            return False

    if no_test and course.get("has_test"):
        return False
    if no_presentation and course.get("has_presentation"):
        return False
    return True


def filter_courses_advanced(
    courses,
    semester_filters=None,
    curriculum_filters=None,
    exam_filter="all",
    exam_max=100,
    report_min=0,
    no_test=False,
    no_presentation=False,
):
    return [
        course
        for course in courses
        if _matches_course_filters(
            course,
            semester_filters=semester_filters,
            curriculum_filters=curriculum_filters,
            exam_filter=exam_filter,
            exam_max=exam_max,
            report_min=report_min,
            no_test=no_test,
            no_presentation=no_presentation,
        )
    ]


def search_courses_page_with_evaluations(page=1, exam_filter="all", exam_max=100, report_min=0, **kwargs):
    nendo = kwargs.get("nendo", "2026")
    page_result = search_courses(page=page, **kwargs)
    enriched_courses = attach_evaluation_only_to_courses(page_result["courses"], nendo)
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


def search_courses_all_pages_with_evaluations_parallel(
    exam_filter="all",
    exam_max=100,
    report_min=0,
    progress_callback=None,
    **kwargs,
):
    nendo = kwargs.get("nendo", "2026")

    def emit_progress(event, **extra):
        if progress_callback is None:
            return
        payload = {
            "event": event,
            **extra,
        }
        try:
            progress_callback(payload)
        except Exception:
            pass

    first_page_result = search_courses(page=1, **kwargs)
    max_page = first_page_result["max_page"]
    total = first_page_result["total"]

    emit_progress("start", total=total, max_page=max_page, pages_completed=0, matched_total=0)

    if total == 0:
        emit_progress("complete", total=0, max_page=1, pages_completed=0, matched_total=0, courses=[])
        return {
            "total": 0,
            "max_page": 1,
            "pages_completed": 0,
            "courses": [],
        }

    page_results = {1: first_page_result}
    if max_page > 1:
        worker_count = min(SEARCH_PAGE_FETCH_WORKERS, max_page - 1)
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {
                executor.submit(search_courses, page=page, **kwargs): page
                for page in range(2, max_page + 1)
            }
            for future in as_completed(futures):
                page = futures[future]
                page_results[page] = future.result()

    page_courses = {}
    page_matches = {page: [] for page in range(1, max_page + 1)}
    page_pending = {}
    code_to_refs = defaultdict(list)

    for page in range(1, max_page + 1):
        courses = []
        for index, course in enumerate(page_results.get(page, {}).get("courses", [])):
            enriched_course = _copy_course(course)
            enriched_course["source_order"] = (page - 1) * SEARCH_PAGE_SIZE + index
            courses.append(enriched_course)
            code = enriched_course.get("code")
            if code:
                code_to_refs[code].append((page, len(courses) - 1))
        page_courses[page] = courses
        page_pending[page] = len([course for course in courses if course.get("code")])

    aggregated_courses = []
    pages_completed = 0

    def finalize_page(page):
        nonlocal pages_completed
        if page_pending.get(page, 0) != 0:
            return
        page_pending[page] = -1
        pages_completed += 1
        new_courses = sorted(page_matches[page], key=lambda course: course.get("source_order", 0))
        aggregated_courses.extend(new_courses)
        aggregated_courses.sort(key=lambda course: course.get("source_order", 0))
        emit_progress(
            "page",
            page=page,
            max_page=max_page,
            pages_completed=pages_completed,
            matched_total=len(aggregated_courses),
            new_courses=[_copy_course(course) for course in new_courses],
        )

    for page in range(1, max_page + 1):
        if page_pending[page] == 0:
            finalize_page(page)

    if code_to_refs:
        cached_evaluations = {}
        uncached_codes = []
        for code in code_to_refs:
            cached = _get_cached_evaluation(nendo, code)
            if cached is None:
                cached = _get_cached_evaluation_from_bundle(nendo, code)
            if cached is None:
                uncached_codes.append(code)
            else:
                cached_evaluations[code] = cached

        def apply_evaluation(code, evaluation):
            refs = code_to_refs.get(code, [])
            for page, index in refs:
                course = page_courses[page][index]
                if evaluation is not None:
                    course["evaluation"] = dict(evaluation)
                    course["has_test"] = bool(evaluation.get("has_test"))
                    course["has_presentation"] = bool(evaluation.get("has_presentation"))
                if _matches_evaluation_filter(evaluation, exam_filter, exam_max, report_min=report_min):
                    page_matches[page].append(course)
                page_pending[page] -= 1
                if page_pending[page] == 0:
                    finalize_page(page)

        for code, evaluation in cached_evaluations.items():
            apply_evaluation(code, evaluation)

        if uncached_codes:
            worker_count = min(EVALUATION_FETCH_WORKERS, len(uncached_codes))
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                futures = {
                    executor.submit(_fetch_evaluation, nendo, code): code
                    for code in uncached_codes
                }
                for future in as_completed(futures):
                    code = futures[future]
                    try:
                        evaluation = future.result()
                    except Exception:
                        evaluation = None
                    apply_evaluation(code, evaluation)

    emit_progress(
        "complete",
        total=total,
        max_page=max_page,
        pages_completed=pages_completed,
        matched_total=len(aggregated_courses),
        courses=[_copy_course(course) for course in aggregated_courses],
    )
    return {
        "total": total,
        "max_page": max_page,
        "pages_completed": pages_completed,
        "courses": [_copy_course(course) for course in aggregated_courses],
    }


def search_courses_advanced(
    page=1,
    all_pages=False,
    max_results=None,
    semester_filters=None,
    curriculum_filters=None,
    exam_filter="all",
    exam_max=100,
    report_min=0,
    no_test=False,
    no_presentation=False,
    timeout_seconds=None,
    progress_callback=None,
    **kwargs,
):
    nendo = kwargs.get("nendo", "2026")
    semester_filters = semester_filters or []
    curriculum_filters = curriculum_filters or []

    if max_results is not None and max_results < 1:
        raise ValueError("max_results must be at least 1")
    if timeout_seconds is not None and timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be greater than 0")

    def emit_progress(event, **extra):
        if progress_callback is None:
            return
        payload = {
            "event": event,
            "elapsed_seconds": time.monotonic() - start_time,
            **extra,
        }
        try:
            progress_callback(payload)
        except Exception:
            pass

    start_time = time.monotonic()
    deadline = start_time + timeout_seconds if timeout_seconds is not None else None
    first_page_result = search_courses(page=page, **kwargs)
    max_page = first_page_result["max_page"]
    pages_to_scan = [page]
    if all_pages or max_results is not None:
        pages_to_scan = list(range(page, max_page + 1))

    collected_courses = []
    complete_results = True
    scanned_pages = 0
    stopped_reason = None

    emit_progress(
        "start",
        start_page=page,
        total=first_page_result["total"],
        max_page=max_page,
        pages_planned=len(pages_to_scan),
        all_pages=all_pages,
        max_results=max_results,
        timeout_seconds=timeout_seconds,
    )

    for page_index, current_page in enumerate(pages_to_scan):
        if page_index > 0 and deadline is not None and time.monotonic() >= deadline:
            complete_results = False
            stopped_reason = "timeout"
            emit_progress(
                "timeout",
                page=current_page,
                pages_fetched=scanned_pages,
                pages_planned=len(pages_to_scan),
                matched_total=len(collected_courses),
                timeout_seconds=timeout_seconds,
            )
            break

        scanned_pages += 1
        page_result = first_page_result if page_index == 0 else search_courses(page=current_page, **kwargs)
        enriched_courses = attach_evaluations_to_courses(page_result["courses"], nendo)
        for course in enriched_courses:
            course["source_page"] = current_page

        filtered_courses = filter_courses_advanced(
            enriched_courses,
            semester_filters=semester_filters,
            curriculum_filters=curriculum_filters,
            exam_filter=exam_filter,
            exam_max=exam_max,
            report_min=report_min,
            no_test=no_test,
            no_presentation=no_presentation,
        )

        collected_courses.extend(filtered_courses)
        emit_progress(
            "page",
            page=current_page,
            page_index=scanned_pages,
            pages_planned=len(pages_to_scan),
            matched_on_page=len(filtered_courses),
            matched_total=len(collected_courses),
        )
        if max_results is not None and len(collected_courses) >= max_results:
            collected_courses = collected_courses[:max_results]
            if current_page < max_page:
                complete_results = False
                stopped_reason = "max_results"
                emit_progress(
                    "limit_reached",
                    page=current_page,
                    pages_fetched=scanned_pages,
                    pages_planned=len(pages_to_scan),
                    matched_total=len(collected_courses),
                    max_results=max_results,
                )
            break

    result = {
        "page": page,
        "total": first_page_result["total"],
        "max_page": max_page,
        "pages_fetched": scanned_pages,
        "all_pages": all_pages,
        "complete_results": complete_results,
        "timed_out": stopped_reason == "timeout",
        "timeout_seconds": timeout_seconds,
        "stopped_reason": stopped_reason,
        "returned_count": len(collected_courses),
        "courses": collected_courses,
        "filters": {
            "semester": semester_filters,
            "curriculum": curriculum_filters,
            "exam_filter": exam_filter,
            "exam_max": exam_max,
            "report_min": report_min,
            "no_test": no_test,
            "no_presentation": no_presentation,
            "max_results": max_results,
        },
    }
    emit_progress(
        "complete",
        pages_fetched=scanned_pages,
        pages_planned=len(pages_to_scan),
        matched_total=len(collected_courses),
        complete_results=complete_results,
        stopped_reason=stopped_reason,
    )
    return result


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


def easy_search_advanced(
    page=1,
    all_pages=False,
    max_results=None,
    semester_filters=None,
    curriculum_filters=None,
    exam_filter="all",
    exam_max=100,
    report_min=0,
    no_test=False,
    no_presentation=False,
    timeout_seconds=None,
    progress_callback=None,
    **kwargs,
):
    params = resolve_params(**kwargs)
    return search_courses_advanced(
        page=page,
        all_pages=all_pages,
        max_results=max_results,
        semester_filters=semester_filters,
        curriculum_filters=curriculum_filters,
        exam_filter=exam_filter,
        exam_max=exam_max,
        report_min=report_min,
        no_test=no_test,
        no_presentation=no_presentation,
        timeout_seconds=timeout_seconds,
        progress_callback=progress_callback,
        **params,
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
        detail = get_structured_syllabus_detail(url=url, nendo=nendo, kodo_2=kodo_2)
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


def safe_search_advanced(
    page=1,
    all_pages=False,
    max_results=None,
    semester_filters=None,
    curriculum_filters=None,
    exam_filter="all",
    exam_max=100,
    report_min=0,
    no_test=False,
    no_presentation=False,
    timeout_seconds=None,
    progress_callback=None,
    **kwargs,
):
    try:
        result = search_courses_advanced(
            page=page,
            all_pages=all_pages,
            max_results=max_results,
            semester_filters=semester_filters,
            curriculum_filters=curriculum_filters,
            exam_filter=exam_filter,
            exam_max=exam_max,
            report_min=report_min,
            no_test=no_test,
            no_presentation=no_presentation,
            timeout_seconds=timeout_seconds,
            progress_callback=progress_callback,
            **kwargs,
        )
        if result["total"] == 0:
            return _ok({"total": 0, "courses": [], "max_page": 1, "page": page, "note": "no_results"})
        return _ok(result)
    except requests.exceptions.RequestException as e:
        return _err("network_error", str(e))
    except ValueError as e:
        return _err("invalid_params", str(e))
    except Exception as e:
        return _err("parse_error", str(e))


def search_and_detail(top_n=5, include_detail=True, **kwargs):
    """Search courses and optionally fetch full syllabus details for top N results.

    Accepts human-readable params via resolve_params().
    Returns structured response with courses enriched with full syllabus data.
    """
    try:
        params = resolve_params(**kwargs)
        nendo = params.get("nendo", "2026")
        result = search_courses(page=1, **params)

        if result["total"] == 0:
            return _ok({"total": 0, "courses": [], "note": "no_results"})

        courses = result["courses"][:top_n]

        if include_detail:
            for course in courses:
                code = course.get("code")
                if code:
                    try:
                        detail = get_syllabus_detail(nendo=nendo, kodo_2=code)
                        course["syllabus"] = detail
                    except Exception:
                        course["syllabus"] = None

        return _ok({
            "total": result["total"],
            "max_page": result["max_page"],
            "showing": len(courses),
            "courses": courses,
        })
    except requests.exceptions.RequestException as e:
        return _err("network_error", str(e))
    except Exception as e:
        return _err("parse_error", str(e))


def _merge_course_with_structured_detail(course, structured_detail):
    merged = _copy_course(course)
    structured_detail = structured_detail or {}

    for key in STRUCTURED_DETAIL_SUMMARY_KEYS:
        incoming = structured_detail.get(key)
        if _is_empty_value(incoming):
            continue
        if key == "curriculum":
            labels = list(merged.get("curriculum") or [])
            for label in incoming:
                if label not in labels:
                    labels.append(label)
            merged["curriculum"] = labels
            continue
        if key == "curriculum_text":
            merged[key] = _dedupe_lines("\n".join(filter(None, [merged.get(key, ""), incoming])))
            continue
        if key == "notice":
            merged[key] = _dedupe_lines("\n".join(filter(None, [merged.get(key, ""), incoming])))
            continue
        if not merged.get(key):
            merged[key] = copy.deepcopy(incoming)
        elif key in {"credits", "reg_method", "withdrawal_available", "format"}:
            merged[key] = copy.deepcopy(incoming)

    if structured_detail.get("evaluation"):
        merged["evaluation"] = copy.deepcopy(structured_detail["evaluation"])
    if structured_detail.get("detail_fields"):
        merged["detail_fields"] = copy.deepcopy(structured_detail["detail_fields"])
    if structured_detail.get("detail_field_labels"):
        merged["detail_field_labels"] = dict(structured_detail["detail_field_labels"])
    if structured_detail.get("raw_detail"):
        merged["raw_detail"] = copy.deepcopy(structured_detail["raw_detail"])
    if structured_detail.get("nendo"):
        merged["nendo"] = structured_detail["nendo"]
    return merged


def _search_courses_with_details(
    page=1,
    top_n=5,
    all_results=False,
    all_pages=False,
    max_results=None,
    semester_filters=None,
    curriculum_filters=None,
    exam_filter="all",
    exam_max=100,
    report_min=0,
    no_test=False,
    no_presentation=False,
    timeout_seconds=None,
    progress_callback=None,
    **kwargs,
):
    params = resolve_params(**kwargs)
    nendo = params.get("nendo", "2026")
    search_result = search_courses_advanced(
        page=page,
        all_pages=all_pages,
        max_results=max_results,
        semester_filters=semester_filters,
        curriculum_filters=curriculum_filters,
        exam_filter=exam_filter,
        exam_max=exam_max,
        report_min=report_min,
        no_test=no_test,
        no_presentation=no_presentation,
        timeout_seconds=timeout_seconds,
        progress_callback=progress_callback,
        **params,
    )

    courses = search_result["courses"]
    if not all_results:
        courses = courses[:top_n]

    bundles = get_course_bundle_batch(nendo, [course["code"] for course in courses if course.get("code")])
    enriched_courses = []
    for course in courses:
        bundle = bundles.get(course.get("code")) or {}
        structured_detail = _build_structured_syllabus_detail(bundle, nendo=nendo, kodo_2=course.get("code"))
        enriched_courses.append(_merge_course_with_structured_detail(course, structured_detail))

    result = dict(search_result)
    result["courses"] = enriched_courses
    result["showing"] = len(enriched_courses)
    result["detail_scope"] = "all_results" if all_results else f"top_{len(enriched_courses)}"
    return result


def search_and_detail_parallel(
    top_n=5,
    all_results=False,
    page=1,
    all_pages=False,
    max_results=None,
    semester_filters=None,
    curriculum_filters=None,
    exam_filter="all",
    exam_max=100,
    report_min=0,
    no_test=False,
    no_presentation=False,
    timeout_seconds=None,
    progress_callback=None,
    **kwargs,
):
    """Search courses and attach structured syllabus details."""
    try:
        result = _search_courses_with_details(
            page=page,
            top_n=top_n,
            all_results=all_results,
            all_pages=all_pages,
            max_results=max_results,
            semester_filters=semester_filters,
            curriculum_filters=curriculum_filters,
            exam_filter=exam_filter,
            exam_max=exam_max,
            report_min=report_min,
            no_test=no_test,
            no_presentation=no_presentation,
            timeout_seconds=timeout_seconds,
            progress_callback=progress_callback,
            **kwargs,
        )

        if result["total"] == 0:
            return _ok({"total": 0, "courses": [], "note": "no_results"})
        return _ok(result)
    except requests.exceptions.RequestException as e:
        return _err("network_error", str(e))
    except ValueError as e:
        return _err("invalid_params", str(e))
    except Exception as e:
        return _err("parse_error", str(e))


# ---------------------------------------------------------------------------
# Course comparison & schedule conflict detection
# ---------------------------------------------------------------------------

def compare_courses(codes, nendo="2026", fields=None):
    """Compare multiple courses side by side.

    Args:
        codes: list of course codes (e.g. ["AF182", "AF301"])
        nendo: academic year
        fields: optional list of detail fields to include. If None, include all.

    Returns structured response with comparison table.
    """
    if not codes or len(codes) < 2:
        return _err("invalid_params", "At least 2 course codes required for comparison")
    if len(codes) > 10:
        return _err("invalid_params", "Maximum 10 courses can be compared at once")

    try:
        # Fetch details in parallel
        details = {}
        worker_count = min(6, len(codes))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {}
            for code in codes:
                future = executor.submit(get_syllabus_detail, nendo=nendo, kodo_2=code)
                futures[future] = code
            for future in as_completed(futures):
                code = futures[future]
                try:
                    details[code] = future.result()
                except Exception:
                    details[code] = None

        # Also get evaluation info
        evaluations = get_evaluation_batch(nendo, codes)

        # Build comparison
        comparison = []
        all_keys = set()
        for code in codes:
            if details.get(code):
                all_keys.update(details[code].keys())

        if fields:
            all_keys = all_keys.intersection(fields)

        for code in codes:
            entry = {
                "code": code,
                "detail": {},
                "evaluation": evaluations.get(code),
            }
            detail = details.get(code) or {}
            for key in sorted(all_keys):
                value = detail.get(key, "")
                # Convert table-type values to string summary
                if isinstance(value, dict) and value.get("type") == "table":
                    rows = value.get("rows", [])
                    value = "; ".join([" | ".join(row) for row in rows[:5]])
                entry["detail"][key] = value
            comparison.append(entry)

        return _ok({
            "courses": comparison,
            "fields": sorted(all_keys),
            "count": len(codes),
        })
    except requests.exceptions.RequestException as e:
        return _err("network_error", str(e))
    except Exception as e:
        return _err("parse_error", str(e))


def _parse_schedule_slots(schedule_str):
    """Parse schedule string like '月1/水3' into a set of (day, period) tuples."""
    slots = set()
    if not schedule_str:
        return slots
    days = {"月": "月", "火": "火", "水": "水", "木": "木", "金": "金", "土": "土"}
    for part in re.split(r"[/／、,\s]+", schedule_str):
        part = part.strip()
        if not part:
            continue
        for day_char in days:
            if day_char in part:
                periods = re.findall(r"(\d)", part)
                for p in periods:
                    slots.add((day_char, p))
    return slots


def check_schedule_conflicts(course_list):
    """Check for time conflicts among a list of courses.

    Args:
        course_list: list of dicts, each with at least "code" and "schedule" keys.
                     Example: [{"code": "AF182", "schedule": "月1"}, {"code": "BX301", "schedule": "月1/水3"}]

    Returns structured response with conflicts found.
    """
    if not course_list:
        return _ok({"conflicts": [], "has_conflicts": False})

    # Parse all schedules
    parsed = []
    for course in course_list:
        code = course.get("code", "unknown")
        name = course.get("name", "")
        schedule = course.get("schedule", "")
        slots = _parse_schedule_slots(schedule)
        parsed.append({"code": code, "name": name, "schedule": schedule, "slots": slots})

    # Find conflicts
    conflicts = []
    for i in range(len(parsed)):
        for j in range(i + 1, len(parsed)):
            overlap = parsed[i]["slots"] & parsed[j]["slots"]
            if overlap:
                conflicts.append({
                    "course_a": {"code": parsed[i]["code"], "name": parsed[i]["name"], "schedule": parsed[i]["schedule"]},
                    "course_b": {"code": parsed[j]["code"], "name": parsed[j]["name"], "schedule": parsed[j]["schedule"]},
                    "overlapping_slots": [f"{day}{period}" for day, period in sorted(overlap)],
                })

    return _ok({
        "conflicts": conflicts,
        "has_conflicts": len(conflicts) > 0,
        "total_courses": len(course_list),
    })


def build_timetable(course_list):
    """Build a weekly timetable grid from a list of courses.

    Args:
        course_list: list of dicts with "code", "name", "schedule" keys.

    Returns a timetable grid structure.
    """
    days = ["月", "火", "水", "木", "金", "土"]
    periods = ["1", "2", "3", "4", "5", "6"]

    grid = {day: {period: [] for period in periods} for day in days}

    for course in course_list:
        code = course.get("code", "")
        name = course.get("name", "")
        slots = _parse_schedule_slots(course.get("schedule", ""))
        for day, period in slots:
            if day in grid and period in grid[day]:
                grid[day][period].append({"code": code, "name": name})

    # Check for conflicts
    conflicts = []
    for day in days:
        for period in periods:
            if len(grid[day][period]) > 1:
                conflicts.append({
                    "slot": f"{day}{period}",
                    "courses": grid[day][period],
                })

    return _ok({
        "grid": grid,
        "conflicts": conflicts,
        "has_conflicts": len(conflicts) > 0,
    })


# ---------------------------------------------------------------------------
# Natural language query parsing
# ---------------------------------------------------------------------------

_DAY_NAMES = ["月", "火", "水", "木", "金", "土"]

_CAMPUS_KEYWORDS = {
    "池袋": "1",
    "新座": "2",
}

_FORMAT_KEYWORDS = {
    "ハイフレックス": "7",
    "オンデマンド": "5",
    "オンライン": "3",
    "対面": "1",
}

_SEMESTER_KEYWORDS = ["春学期", "秋学期", "通年"]

_PARTICLES = re.compile(r"[のでははがをにと]+$")
_NOISE_WORDS = re.compile(r"(キャンパス|授業|科目|講義|の|　)+")
_WHITESPACE_COLLAPSE = re.compile(r"\s+")


def parse_natural_query(query):
    """Parse a free-form Japanese query into structured search parameters."""
    remaining = query.strip()
    params = {}
    schedule_filter = []
    semester_filter = []

    # --- Campus detection ---
    for keyword, value in _CAMPUS_KEYWORDS.items():
        if keyword in remaining:
            params["bunrui12"] = value
            remaining = remaining.replace(keyword, "", 1)

    # --- Department detection (longest match first) ---
    sorted_departments = sorted(GAKUBU_REVERSE.keys(), key=len, reverse=True)
    for dept_name in sorted_departments:
        if dept_name in remaining:
            params["gakubu"] = GAKUBU_REVERSE[dept_name]
            remaining = remaining.replace(dept_name, "", 1)
            break

    # --- Format detection (order matters: check longer strings first) ---
    for keyword, value in _FORMAT_KEYWORDS.items():
        if keyword in remaining:
            params["bunrui3"] = value
            remaining = remaining.replace(keyword, "", 1)
            break

    # --- Day + period detection ---
    # Match patterns like 月曜2限, 月2限, 月曜2時限, 月2, 月曜日2限
    day_period_pattern = re.compile(
        r"([月火水木金土])曜?日?(\d)[時限]*"
    )
    for m in day_period_pattern.finditer(remaining):
        day = m.group(1)
        period = m.group(2)
        schedule_filter.append(f"{day}{period}")
    remaining = day_period_pattern.sub("", remaining)

    # Match standalone day mentions like 月曜, 月曜日 (no period)
    day_only_pattern = re.compile(r"([月火水木金土])曜日?")
    for m in day_only_pattern.finditer(remaining):
        day = m.group(1)
        # Only add if we don't already have this day with a period
        if not any(sf.startswith(day) for sf in schedule_filter):
            schedule_filter.append(day)
    remaining = day_only_pattern.sub("", remaining)

    # Match standalone period like 2限, 3時限
    period_only_pattern = re.compile(r"(\d)[時限]+")
    for m in period_only_pattern.finditer(remaining):
        period = m.group(1)
        # Only add standalone period if no day+period combos exist
        if not schedule_filter:
            schedule_filter.append(period)
    remaining = period_only_pattern.sub("", remaining)

    # --- Semester detection ---
    for keyword in _SEMESTER_KEYWORDS:
        if keyword in remaining:
            semester_filter.append(keyword)
            remaining = remaining.replace(keyword, "", 1)
    # Short forms: 春 or 秋 (only if 春学期/秋学期 not already matched)
    if not semester_filter:
        if "春" in remaining:
            semester_filter.append("春学期")
            remaining = remaining.replace("春", "", 1)
        if "秋" in remaining:
            semester_filter.append("秋学期")
            remaining = remaining.replace("秋", "", 1)

    # --- Remaining text becomes kamokumei ---
    remaining = remaining.strip()
    remaining = _PARTICLES.sub("", remaining)
    remaining = re.sub(r"^[のでははがをにと]+", "", remaining)
    remaining = _NOISE_WORDS.sub(" ", remaining)
    remaining = _WHITESPACE_COLLAPSE.sub(" ", remaining).strip()

    if remaining:
        params["kamokumei"] = remaining

    params["schedule_filter"] = schedule_filter
    params["semester_filter"] = semester_filter

    return params


def natural_search(query, page=1):
    """Search using a natural language query string."""
    parsed = parse_natural_query(query)
    schedule_filter = parsed.pop("schedule_filter", [])
    semester_filter = parsed.pop("semester_filter", [])

    result = search_courses(page=page, **parsed)

    # Apply schedule filter client-side
    if schedule_filter:
        result["courses"] = [
            c for c in result["courses"]
            if any(sf in (c.get("schedule") or "") for sf in schedule_filter)
        ]

    # Apply semester filter client-side
    if semester_filter:
        result["courses"] = [
            c for c in result["courses"]
            if any(sf in (c.get("semester") or "") for sf in semester_filter)
        ]

    result["parsed_params"] = parsed
    result["schedule_filter"] = schedule_filter
    result["semester_filter"] = semester_filter

    return _ok(result)
