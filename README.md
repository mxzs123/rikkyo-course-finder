# Rikkyo Syllabus — 立教大学 履修サーチ

立教大学の公式シラバスシステムを解析し、より使いやすい検索 UI・CLI・AI 連携機能を提供するツールです。

> **注意**: 本ツールは立教大学公式のものではありません。上流サーバーへの過度なリクエストは避けてください。

## Features

- **Web UI** — Flask ベースの検索画面。曜日時限グリッド、評価方式フィルタ、シラバス詳細モーダル
- **CLI** — ターミナルから JSON 形式で結果を取得可能。AI エージェントから subprocess 経由で呼び出す用途にも適しています
- **MCP Server** — Model Context Protocol 対応。Claude / Cursor などの AI からツールとして直接呼び出せます
- **自然言語検索** — `月曜2限の経済学部の英語` のような自由文から検索パラメータを自動抽出
- **科目比較** — 複数科目のシラバスを取得し、並べて比較
- **時間割衝突検出** — 選択した科目間の曜日時限の重複を検出

## Quick Start

### Install

```bash
pip install -e .            # CLI + core
pip install -e ".[web]"     # + Web UI
pip install -e ".[mcp]"     # + MCP server
pip install -e ".[all]"     # everything
```

### Web UI

```bash
python3 app.py
# → http://localhost:5050
```

### CLI

```bash
# Search
python3 cli.py search --department 文学部 --course-name 英語

# Exhaustive multi-page scan with stderr progress and a 3-minute timeout
python3 cli.py search --keyword ゼミ --semester 春学期 --all-pages --timeout 180

# Syllabus detail
python3 cli.py detail --code AF182

# Search + fetch full syllabus for top N
python3 cli.py search-detail --department 経済学部 --top 3

# Natural language search
python3 cli.py nl-search "月曜2限の池袋キャンパスの対面授業"

# Compare courses
python3 cli.py compare --codes AF182,AF181

# Check schedule conflicts
python3 cli.py conflicts --courses '[{"code":"A1","name":"Math","schedule":"月1"},{"code":"A2","name":"English","schedule":"月1"}]'

# Build timetable
python3 cli.py timetable --courses '[{"code":"A1","name":"Math","schedule":"月1"},{"code":"A2","name":"English","schedule":"水3"}]'

# API schema (for AI consumption)
python3 cli.py schema

# List all valid option values
python3 cli.py list-options
```

### MCP Server

```bash
python3 mcp_server.py
```

Or add to your AI tool config:

```json
{
  "mcpServers": {
    "rikkyo-syllabus": {
      "command": "python3",
      "args": ["mcp_server.py"],
      "cwd": "/path/to/rikkyo-syllabus"
    }
  }
}
```

## AI Integration

### For AI agents calling via subprocess

```bash
# 1. Get the schema first
python3 cli.py schema

# 2. Search with human-readable params
python3 cli.py search --department 文学部 --campus 池袋 --format 対面

# 3. All output is structured JSON with {"ok": true/false, "data": ...}
```

`cli.py schema` now marks each parameter with `server_side: true/false`. Parameters such as `semester`, `curriculum`, `exam_filter`, `no_test`, and `no_presentation` are local post-filters, so pair them with `--all-pages` when you need exhaustive matches across the full result set.

### For AI agents via Python import

```python
from scraper import easy_search, search_and_detail_parallel, natural_search

# Human-readable params
result = easy_search(department="経済学部", campus="池袋")

# Search + detail in one call
result = search_and_detail_parallel(top_n=3, department="文学部")

# Natural language
result = natural_search("月曜の池袋の対面授業")
```

### Parameter aliases

| CLI / Python arg   | Upstream field  | Example values                    |
|---------------------|-----------------|-----------------------------------|
| `--department`      | `gakubu`        | 文学部, 経済学部, GLAP             |
| `--course-name`     | `kamokumei`     | 英語, データサイエンス             |
| `--teacher`         | `admin36_text`  | 田中                               |
| `--campus`          | `bunrui12`      | 池袋, 新座                         |
| `--format`          | `bunrui3`       | 対面, オンライン, ハイフレックス   |
| `--category`        | `bunrui19`      | 大学, 大学院                       |
| `--registration`    | `bunrui2`       | 抽選登録, 自動登録                 |
| `--course-code`     | `kodo_2`        | AF182                              |

Both Japanese names and numeric IDs are accepted. Partial matching is supported when unambiguous.

## Project Structure

```
scraper.py        # Core: upstream API client, HTML parser, all search/filter logic
app.py            # Web UI: Flask routes
cli.py            # CLI: argparse commands, JSON output
mcp_server.py     # MCP: FastMCP server with 9 tools
templates/        # Web UI: Jinja2 + vanilla JS frontend
```

## Requirements

- Python 3.10+
- `requests`, `beautifulsoup4` (core)
- `flask` (web UI, optional)
- `mcp[cli]` (MCP server, optional)

## License

MIT
