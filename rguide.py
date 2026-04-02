"""R Guide curriculum map: parse official PDFs → course code → category mapping.

Usage:
    # Generate map from downloaded PDFs
    python3 rguide.py generate --major rguide_data/16_eibei.pdf \
                               --kikan rguide_data/16_kikankamoku.pdf \
                               --zenkari rguide_data/kamokuhyo2016.pdf \
                               -o rguide_data/curriculum_map.json

    # Show category for specific codes
    python3 rguide.py lookup --map rguide_data/curriculum_map.json AM311 AL205 FB136
"""
import argparse
import json
import os
import re
import subprocess
import sys


# ---------------------------------------------------------------------------
# PDF text extraction
# ---------------------------------------------------------------------------

def _pdftotext(pdf_path):
    """Extract text from PDF using pdftotext (poppler)."""
    try:
        result = subprocess.run(
            ["pdftotext", "-layout", pdf_path, "-"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            return result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Fallback: try without -layout
    try:
        result = subprocess.run(
            ["pdftotext", pdf_path, "-"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            return result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return ""


# ---------------------------------------------------------------------------
# Parsers for each PDF type
# ---------------------------------------------------------------------------

_CODE_RE = re.compile(r"\b([A-Z]{2}\d{3})\b")

# Category header patterns in major-specific PDFs (e.g. 16_eibei.pdf)
_MAJOR_CATEGORY_PATTERNS = [
    (re.compile(r"必修科目（基幹科目Ａ）"), "基幹A"),
    (re.compile(r"必修科目（指定科目Ａ）"), "指定A"),
    (re.compile(r"必修科目（基幹科目Ｂ）"), "基幹B"),
    (re.compile(r"必修科目（基幹科目Ｃ）"), "基幹C"),
    (re.compile(r"必修科目（基幹科目Ｄ）"), "基幹D"),
    (re.compile(r"選択科目（指定科目Ｂ１）"), "指定B1"),
    (re.compile(r"選択科目（指定科目Ｂ２）"), "指定B2"),
    (re.compile(r"選択科目（指定科目Ｃ）"), "指定C"),
    (re.compile(r"卒業論文"), "卒業論文"),
    (re.compile(r"自由科目"), "自由科目"),
]

# Category header patterns in 基幹科目表 (16_kikankamoku.pdf)
_KIKAN_CATEGORY_PATTERNS = [
    (re.compile(r"必修科目（基幹科目Ａ）"), "基幹A"),
    (re.compile(r"選択科目（基幹科目Ｂ）"), "基幹B"),
    (re.compile(r"選択科目（基幹科目Ｃ）"), "基幹C"),
    (re.compile(r"選択科目（基幹科目Ｄ）"), "基幹D"),
]

# Category patterns in 全学共通科目表 (kamokuhyo2016.pdf)
_ZENKARI_CATEGORY_MAP = {
    "学びの精神 科目群": "学びの精神",
    "1.人間の探究": "多彩な学び(人間の探究)",
    "2.社会への視点": "多彩な学び(社会への視点)",
    "3.芸術・文化への招待": "多彩な学び(芸術・文化への招待)",
    "4.心身への着目": "多彩な学び(心身への着目)",
    "5.自然の理解": "多彩な学び(自然の理解)",
    "6.知識の現場": "多彩な学び(知識の現場)",
    "スポーツ実習科目群": "スポーツ実習",
}

# Prefix-based fallback rules for codes not explicitly in the map
_PREFIX_CATEGORY_RULES = {
    "FH": "学びの精神",
    "FA": "多彩な学び",
    "FB": "多彩な学び",
    "FC": "多彩な学び",
    "FD": "多彩な学び",
    "FE": "多彩な学び",
    "FV": "多彩な学び(領域別A)",
    "FI": "スポーツ実習",
}

# Lines to skip (not course data)
_SKIP_LINE_RE = re.compile(
    r"(科目表|ページ|科\s*目\s*コード|科\s*目\s*名|担\s*当\s*者|"
    r"単位数|開講学期|配当|登録|ナンバリング|備\s*考|"
    r"基幹科目\s*科目表\s*を参照|上記以外の|"
    r"全学共通科目総合系|文学部基幹科目|超過履修分|"
    r"指定科目B1.*B2.*C|言語系科目|文学部他学科|他学部科目|"
    r"4大学間|随意科目|教職課程登録者)"
)


def _parse_sections(text, category_patterns):
    """Parse text into {category: [codes]} using ordered header patterns."""
    lines = text.split("\n")
    current_category = None
    result = {}

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        # Check for category header change
        for pattern, category in category_patterns:
            if pattern.search(stripped):
                current_category = category
                break

        if current_category is None:
            continue

        # Skip non-data lines
        if _SKIP_LINE_RE.search(stripped):
            # But still check if there are codes on this line
            codes = _CODE_RE.findall(stripped)
            if not codes:
                continue

        # Extract course codes from this line
        codes = _CODE_RE.findall(stripped)
        for code in codes:
            result.setdefault(current_category, set()).add(code)

    return {cat: sorted(codes) for cat, codes in result.items()}


def parse_major_pdf(pdf_path):
    """Parse major-specific PDF (e.g. 16_eibei.pdf) → {category: [codes]}."""
    text = _pdftotext(pdf_path)
    if not text:
        return {}
    return _parse_sections(text, _MAJOR_CATEGORY_PATTERNS)


def parse_kikan_pdf(pdf_path):
    """Parse 基幹科目 PDF (16_kikankamoku.pdf) → {category: [codes]}."""
    text = _pdftotext(pdf_path)
    if not text:
        return {}
    return _parse_sections(text, _KIKAN_CATEGORY_PATTERNS)


def parse_zenkari_pdf(pdf_path):
    """Parse 全学共通科目 PDF → {category: [codes]}."""
    text = _pdftotext(pdf_path)
    if not text:
        return {}

    lines = text.split("\n")
    current_category = None
    result = {}

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        # Check for category headers
        for header, category in _ZENKARI_CATEGORY_MAP.items():
            if header in stripped:
                current_category = category
                break

        if current_category is None:
            continue

        codes = _CODE_RE.findall(stripped)
        for code in codes:
            result.setdefault(current_category, set()).add(code)

    return {cat: sorted(codes) for cat, codes in result.items()}


# ---------------------------------------------------------------------------
# Build combined curriculum map
# ---------------------------------------------------------------------------

def build_curriculum_map(major_pdf=None, kikan_pdf=None, zenkari_pdf=None,
                         department="文学部", major="英米文学専修", year=2026):
    """Build a combined curriculum map from R Guide PDFs.

    Returns a dict suitable for JSON serialization.
    """
    code_to_category = {}
    category_codes = {}

    # 1. Parse 基幹科目 (shared across 文学部)
    if kikan_pdf and os.path.exists(kikan_pdf):
        kikan = parse_kikan_pdf(kikan_pdf)
        for category, codes in kikan.items():
            category_codes.setdefault(category, []).extend(codes)
            for code in codes:
                code_to_category[code] = category

    # 2. Parse major-specific PDF (overrides 基幹 for any conflicts)
    if major_pdf and os.path.exists(major_pdf):
        major_data = parse_major_pdf(major_pdf)
        for category, codes in major_data.items():
            # Skip 自由科目 section (it's a rule, not specific codes)
            if category == "自由科目":
                continue
            category_codes.setdefault(category, []).extend(codes)
            for code in codes:
                # Major-specific takes precedence for 指定 categories
                if category.startswith("指定") or category == "卒業論文":
                    code_to_category[code] = category
                elif code not in code_to_category:
                    code_to_category[code] = category

    # 3. Parse 全学共通科目
    if zenkari_pdf and os.path.exists(zenkari_pdf):
        zenkari = parse_zenkari_pdf(zenkari_pdf)
        for category, codes in zenkari.items():
            category_codes.setdefault(category, []).extend(codes)
            for code in codes:
                if code not in code_to_category:
                    code_to_category[code] = category

    # Dedupe category_codes
    category_codes = {
        cat: sorted(set(codes)) for cat, codes in category_codes.items()
    }

    sources = []
    for path in [major_pdf, kikan_pdf, zenkari_pdf]:
        if path and os.path.exists(path):
            sources.append(os.path.basename(path))

    return {
        "meta": {
            "year": year,
            "department": department,
            "major": major,
            "sources": sources,
        },
        "code_to_category": code_to_category,
        "category_codes": category_codes,
        "prefix_rules": dict(_PREFIX_CATEGORY_RULES),
    }


# ---------------------------------------------------------------------------
# Loading and querying
# ---------------------------------------------------------------------------

_loaded_map = None
_loaded_map_path = None


def load_curriculum_map(json_path):
    """Load a pre-generated curriculum map from JSON."""
    global _loaded_map, _loaded_map_path
    if _loaded_map is not None and _loaded_map_path == json_path:
        return _loaded_map
    with open(json_path, "r", encoding="utf-8") as f:
        _loaded_map = json.load(f)
    _loaded_map_path = json_path
    return _loaded_map


def find_default_map():
    """Look for curriculum_map.json in common locations."""
    candidates = [
        os.path.join(os.path.dirname(__file__), "rguide_data", "curriculum_map.json"),
        os.path.join(os.getcwd(), "rguide_data", "curriculum_map.json"),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


def lookup_category(code, curriculum_map=None):
    """Return the curriculum category for a course code.

    Priority:
    1. Exact match in code_to_category
    2. Prefix-based rule
    3. None
    """
    if curriculum_map is None:
        return None

    c2c = curriculum_map.get("code_to_category", {})
    if code in c2c:
        return c2c[code]

    # Prefix fallback
    prefix_rules = curriculum_map.get("prefix_rules", _PREFIX_CATEGORY_RULES)
    prefix = code[:2] if len(code) >= 2 else ""
    if prefix in prefix_rules:
        return prefix_rules[prefix]

    return None


def annotate_courses(courses, curriculum_map):
    """Add rguide_category to each course dict (in-place). Returns courses."""
    if not curriculum_map:
        return courses
    for course in courses:
        code = course.get("code", "")
        cat = lookup_category(code, curriculum_map)
        if cat:
            course["rguide_category"] = cat
    return courses


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _generate_and_save(major_pdf, kikan_pdf, zenkari_pdf, department, major_name, year, output):
    """Build curriculum map and save to JSON. Returns the map."""
    cmap = build_curriculum_map(
        major_pdf=major_pdf,
        kikan_pdf=kikan_pdf,
        zenkari_pdf=zenkari_pdf,
        department=department,
        major=major_name,
        year=year,
    )
    os.makedirs(os.path.dirname(output) or ".", exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        json.dump(cmap, f, ensure_ascii=False, indent=2)
        f.write("\n")

    c2c = cmap["code_to_category"]
    cats = cmap["category_codes"]
    print(f"Generated {output}", file=sys.stderr)
    print(f"  Total codes mapped: {len(c2c)}", file=sys.stderr)
    for cat in sorted(cats.keys()):
        print(f"  {cat}: {len(cats[cat])} codes", file=sys.stderr)
    return cmap


def cmd_generate(args):
    _generate_and_save(
        major_pdf=args.major,
        kikan_pdf=args.kikan,
        zenkari_pdf=args.zenkari,
        department=args.department,
        major_name=getattr(args, "major_name", "英米文学専修"),
        year=args.year,
        output=args.output,
    )


def cmd_lookup(args):
    cmap = load_curriculum_map(args.map)
    results = {}
    for code in args.codes:
        cat = lookup_category(code, cmap)
        results[code] = cat or "(不明)"
    json.dump(results, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def main():
    parser = argparse.ArgumentParser(description="R Guide curriculum map tool")
    sub = parser.add_subparsers(dest="command", required=True)

    # generate
    sp_gen = sub.add_parser("generate", help="Parse PDFs and generate curriculum map JSON")
    sp_gen.add_argument("--major", help="Major-specific PDF (e.g. 16_eibei.pdf)")
    sp_gen.add_argument("--kikan", help="基幹科目 PDF (16_kikankamoku.pdf)")
    sp_gen.add_argument("--zenkari", help="全学共通科目 PDF (kamokuhyo2016.pdf)")
    sp_gen.add_argument("--department", default="文学部", help="Department name")
    sp_gen.add_argument("--major-name", default="英米文学専修", help="Major name")
    sp_gen.add_argument("--year", type=int, default=2026, help="Academic year")
    sp_gen.add_argument("-o", "--output", default="rguide_data/curriculum_map.json",
                        help="Output JSON path")
    sp_gen.set_defaults(func=cmd_generate)

    # lookup
    sp_look = sub.add_parser("lookup", help="Look up category for course codes")
    sp_look.add_argument("--map", default="rguide_data/curriculum_map.json",
                         help="Curriculum map JSON path")
    sp_look.add_argument("codes", nargs="+", help="Course codes to look up")
    sp_look.set_defaults(func=cmd_lookup)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
