# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A course search tool for Rikkyo University (立教大学) that reverse-engineers the official syllabus system at `https://sy.rikkyo.ac.jp/web/`. Three entry points: Web UI (Flask), CLI (argparse, JSON output), MCP server (FastMCP).

## Commands

```bash
# Install
pip install -e ".[all]"

# Run web UI (localhost:5050)
python3 app.py

# Run tests
python3 -m unittest test_cli.py -v

# CLI examples
python3 cli.py search --department 文学部 --semester 春学期 --all-pages
python3 cli.py detail --code AF182
python3 cli.py search-detail --department 文学部 --all-pages --all-results --report-min 60
python3 cli.py schema          # shows all params with server_side flag

# MCP server
python3 mcp_server.py
```

## Architecture

**scraper.py** is the core — everything else is a thin wrapper.

- Upstream returns full HTML (not JSON). BeautifulSoup parses search results from `table.searchShow` and detail pages from `table.attribute` + `div.subjectContents`.
- Page 1 is POST, pages 2+ are GET with same params plus `page=N`. 20 results per page.
- `*_MAP` dicts (GAKUBU_MAP, BUNRUI3_MAP, etc.) map numeric upstream form IDs to Japanese labels. Both names and IDs are accepted as input; `resolve_params()` handles the translation.
- Registration method is encoded as icon images (`ri_icon01.jpg`–`ri_icon06.jpg`), decoded via `ICON_MAP`.

**Search filters have two tiers:**
- **Server-side** (sent to upstream): department, course_name, teacher, campus, format, category, registration, course_code, numbering, keyword
- **Client-side** (local post-filter): semester, curriculum, exam_filter, no_test, no_presentation, exam_max, report_min

Client-side filters only see the fetched pages. Use `--all-pages` for exhaustive matching. `cli.py schema` marks each param with `server_side: true/false`.

**Detail returns a structured three-layer response:**
- Top-level canonical keys: `code`, `schedule`, `reg_method`, `credits`, `curriculum`, `curriculum_text`, `evaluation`, etc.
- `detail_fields` / `detail_field_labels`: normalized field dict for stable scripting.
- `raw_detail`: original Japanese key-value pairs from upstream HTML.

**Evaluation breakdown** includes `exam_pct`, `written_pct` (alias `written_exam_pct`), `report_pct`, `in_class_pct`, `other_pct`, plus per-component `has_test` / `has_presentation` flags. Search results also carry `curriculum` and `curriculum_text` extracted from the listing page notes.

**`search-detail`** supports `--all-results` (fetch detail for every matched course, not just `--top N`), combined with `--all-pages`, `--report-min`, `--curriculum`, `--no-test`, etc.

**app.py** — Flask routes: `GET /` (UI), `GET /api/search` (proxy), `GET /api/detail` (structured detail). All endpoints return canonical English keys.

**cli.py** — 8 subcommands (search, detail, search-detail, nl-search, compare, conflicts, timetable, schema, list-options). All output JSON to stdout, progress to stderr. `--year` defaults to current year at runtime.

**mcp_server.py** — FastMCP wrapper around scraper functions. 9 tools mirroring CLI commands. `get_detail` returns the same structured schema as CLI/Flask.

**templates/index.html** — Vanilla JS single-page app. No build step. Time-slot grid + semester checkboxes do client-side filtering on already-fetched results. Detail modal renders canonical fields from structured detail.

## Upstream API Quirks

- No auth required.
- The `keyword` field must be set to `"key"` (hardcoded upstream behavior).
- The `-find` field value is the literal submit button text: `" 検　索 "`.
- Total count is extracted from `<h2>` text as `（N件）`.
- Japanese text is inside `<span class="jp">`; use `_jp_text()` helper.

## Testing

Tests are in `test_cli.py`. They test detail parsing, evaluation extraction, multi-page filtering, timeout handling, CLI arg forwarding, progress output, and schema generation. Run with `python3 -m unittest test_cli.py -v`.
