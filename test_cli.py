import io
import json
import sys
import unittest
from unittest.mock import patch

import cli
import scraper


DETAIL_HTML = """
<html><body>
  <table class="attribute">
    <tr>
      <td><span class="jp">単位</span></td>
      <td><span class="jp">2</span></td>
      <td><span class="jp">学期</span></td>
      <td><span class="jp">春学期</span></td>
    </tr>
  </table>
  <div class="subjectContents">
    <h3>【注意事項 / Notice】</h3>
    <p><span class="jp">2016年度以降入学者：多彩な学び</span></p>
    <p><span class="jp">2015年度以前入学者：主題別A</span></p>
    <h3>【成績評価方法・基準 / Evaluation】</h3>
    <table class="schedule" cellspacing="5">
      <tr>
        <th>種類 (Kind)</th>
        <th>割合 (%)</th>
        <th>基準 (Criteria)</th>
      </tr>
      <tr>
        <td>平常点 (In-class Points)</td>
        <td>100</td>
        <td>小テスト(50%) 発表(50%)</td>
      </tr>
      <tr><th colspan="3">備考 (Notes)</th></tr>
      <tr><td colspan="3">最終レポート提出必須</td></tr>
    </table>
  </div>
</body></html>
"""


class ScraperFeatureTests(unittest.TestCase):
    def test_detail_bundle_extracts_curriculum_and_credits(self):
        bundle = scraper._build_detail_bundle_from_html(DETAIL_HTML)

        self.assertEqual(bundle["metadata"]["credits"], "2")
        self.assertEqual(bundle["metadata"]["semester"], "春学期")
        self.assertEqual(bundle["metadata"]["curriculum"], ["多彩な学び", "主題別A"])

    def test_evaluation_parser_detects_hidden_test_and_presentation(self):
        evaluation = scraper._parse_evaluation_info(DETAIL_HTML)

        self.assertTrue(evaluation["has_test"])
        self.assertTrue(evaluation["has_presentation"])
        self.assertEqual(evaluation["written_pct"], 0)
        self.assertIn("最終レポート提出必須", evaluation["notes"])

    def test_structured_detail_uses_canonical_keys(self):
        bundle = scraper._build_detail_bundle_from_html(DETAIL_HTML)
        detail = scraper._build_structured_syllabus_detail(bundle, nendo="2026", kodo_2="AF182")

        self.assertEqual(detail["code"], "AF182")
        self.assertEqual(detail["semester"], "春学期")
        self.assertEqual(detail["credits"], "2")
        self.assertEqual(detail["curriculum"], ["多彩な学び", "主題別A"])
        self.assertIn("evaluation_method", detail["detail_fields"])
        self.assertEqual(detail["detail_field_labels"]["evaluation_method"], "成績評価方法・基準")

    @patch("scraper.attach_evaluations_to_courses")
    @patch("scraper.search_courses")
    def test_search_courses_advanced_supports_multi_page_filters(self, mock_search_courses, mock_attach):
        page_1_courses = [
            {"code": "A1", "name": "A", "semester": "春学期", "has_test": False, "has_presentation": False, "credits": "2"},
            {"code": "B1", "name": "B", "semester": "秋学期", "has_test": False, "has_presentation": False, "credits": "2"},
        ]
        page_2_courses = [
            {"code": "C1", "name": "C", "semester": "春学期", "has_test": False, "has_presentation": False, "credits": "4"},
        ]

        mock_search_courses.side_effect = [
            {"total": 60, "max_page": 3, "courses": page_1_courses},
            {"total": 60, "max_page": 3, "courses": page_2_courses},
        ]
        mock_attach.side_effect = lambda courses, _nendo: courses

        result = scraper.search_courses_advanced(
            page=1,
            max_results=2,
            semester_filters=["春学期"],
            no_test=True,
            nendo="2026",
        )

        self.assertEqual([course["code"] for course in result["courses"]], ["A1", "C1"])
        self.assertEqual(result["pages_fetched"], 2)
        self.assertFalse(result["complete_results"])

    @patch("scraper.attach_evaluations_to_courses")
    @patch("scraper.search_courses")
    @patch("scraper.time.monotonic")
    def test_search_courses_advanced_stops_on_timeout(self, mock_monotonic, mock_search_courses, mock_attach):
        events = []
        mock_search_courses.return_value = {
            "total": 40,
            "max_page": 2,
            "courses": [
                {"code": "A1", "name": "A", "semester": "春学期", "has_test": False, "has_presentation": False},
            ],
        }
        mock_attach.side_effect = lambda courses, _nendo: courses
        mock_monotonic.side_effect = [0, 0, 1, 6, 6, 6]

        result = scraper.search_courses_advanced(
            page=1,
            all_pages=True,
            timeout_seconds=5,
            progress_callback=events.append,
            nendo="2026",
        )

        self.assertEqual(result["pages_fetched"], 1)
        self.assertFalse(result["complete_results"])
        self.assertTrue(result["timed_out"])
        self.assertEqual(result["stopped_reason"], "timeout")
        self.assertEqual([event["event"] for event in events], ["start", "page", "timeout", "complete"])

    @patch("scraper._fetch_evaluation")
    @patch("scraper.search_courses")
    def test_parallel_evaluation_search_reports_progress_and_preserves_order(self, mock_search_courses, mock_fetch_evaluation):
        with scraper._eval_cache_lock:
            scraper._eval_cache.clear()
        with scraper._detail_bundle_cache_lock:
            scraper._detail_bundle_cache.clear()

        mock_search_courses.side_effect = [
            {
                "total": 40,
                "max_page": 2,
                "courses": [
                    {"code": "A1", "name": "A", "semester": "春学期"},
                    {"code": "B1", "name": "B", "semester": "春学期"},
                ],
            },
            {
                "total": 40,
                "max_page": 2,
                "courses": [
                    {"code": "C1", "name": "C", "semester": "秋学期"},
                ],
            },
        ]

        eval_map = {
            "A1": {"exam_pct": 0, "report_pct": 0, "in_class_pct": 100, "has_exam": False, "has_report": False},
            "B1": {"exam_pct": 70, "report_pct": 0, "in_class_pct": 30, "has_exam": True, "has_report": False},
            "C1": {"exam_pct": 0, "report_pct": 20, "in_class_pct": 100, "has_exam": False, "has_report": True},
        }
        mock_fetch_evaluation.side_effect = lambda _nendo, code: eval_map.get(code)

        events = []
        result = scraper.search_courses_all_pages_with_evaluations_parallel(
            exam_filter="no-exam",
            progress_callback=events.append,
            nendo="2026",
        )

        self.assertEqual([course["code"] for course in result["courses"]], ["A1", "C1"])
        self.assertEqual(result["pages_completed"], 2)
        self.assertEqual(events[0]["event"], "start")
        self.assertEqual(events[-1]["event"], "complete")
        self.assertEqual(sum(1 for event in events if event["event"] == "page"), 2)


class CliFeatureTests(unittest.TestCase):
    @patch("cli.safe_search_advanced")
    def test_cli_search_forwards_new_filters(self, mock_safe_search_advanced):
        mock_safe_search_advanced.return_value = {"ok": True, "data": {"courses": []}}
        stdout = io.StringIO()

        argv = [
            "cli.py",
            "search",
            "--semester", "春学期",
            "--curriculum", "学びの精神",
            "--no-test",
            "--no-presentation",
            "--all-pages",
            "--max-results", "50",
        ]

        with patch.object(sys, "argv", argv), patch("sys.stdout", stdout):
            cli.main()

        _, kwargs = mock_safe_search_advanced.call_args
        self.assertEqual(kwargs["semester_filters"], ["春学期"])
        self.assertEqual(kwargs["curriculum_filters"], ["学びの精神"])
        self.assertTrue(kwargs["no_test"])
        self.assertTrue(kwargs["no_presentation"])
        self.assertTrue(kwargs["all_pages"])
        self.assertEqual(kwargs["max_results"], 50)
        self.assertEqual(kwargs["timeout_seconds"], cli.DEFAULT_MULTI_PAGE_TIMEOUT)
        self.assertEqual(kwargs["nendo"], cli.DEFAULT_ACADEMIC_YEAR)
        self.assertTrue(callable(kwargs["progress_callback"]))

        payload = json.loads(stdout.getvalue())
        self.assertTrue(payload["ok"])

    @patch("cli.safe_search_advanced")
    def test_cli_search_emits_progress_to_stderr_for_multi_page_scan(self, mock_safe_search_advanced):
        stdout = io.StringIO()
        stderr = io.StringIO()

        def fake_safe_search_advanced(**kwargs):
            kwargs["progress_callback"]({
                "event": "start",
                "start_page": 1,
                "total": 120,
                "pages_planned": 6,
                "timeout_seconds": 30,
                "elapsed_seconds": 0.0,
            })
            kwargs["progress_callback"]({
                "event": "page",
                "page_index": 1,
                "page": 1,
                "pages_planned": 6,
                "matched_on_page": 3,
                "matched_total": 3,
                "elapsed_seconds": 1.2,
            })
            kwargs["progress_callback"]({
                "event": "complete",
                "pages_fetched": 6,
                "pages_planned": 6,
                "matched_total": 18,
                "complete_results": True,
                "elapsed_seconds": 9.5,
            })
            return {"ok": True, "data": {"courses": []}}

        mock_safe_search_advanced.side_effect = fake_safe_search_advanced

        argv = [
            "cli.py",
            "search",
            "--all-pages",
            "--timeout", "30",
        ]

        with patch.object(sys, "argv", argv), patch("sys.stdout", stdout), patch("sys.stderr", stderr):
            cli.main()

        progress_output = stderr.getvalue()
        self.assertIn("[progress] scanning 6 pages from page 1", progress_output)
        self.assertIn("[progress] page 1/6", progress_output)
        self.assertIn("[progress] done after 9.5s", progress_output)

    @patch("cli.safe_detail")
    def test_cli_detail_uses_current_year_by_default(self, mock_safe_detail):
        mock_safe_detail.return_value = {"ok": True, "data": {"code": "AF182"}}
        stdout = io.StringIO()

        argv = [
            "cli.py",
            "detail",
            "--code", "AF182",
        ]

        with patch.object(sys, "argv", argv), patch("sys.stdout", stdout):
            cli.main()

        _, kwargs = mock_safe_detail.call_args
        self.assertEqual(kwargs["nendo"], cli.DEFAULT_ACADEMIC_YEAR)

    @patch("cli.search_and_detail_parallel")
    def test_cli_search_detail_supports_all_results_and_local_filters(self, mock_search_and_detail_parallel):
        mock_search_and_detail_parallel.return_value = {"ok": True, "data": {"courses": []}}
        stdout = io.StringIO()

        argv = [
            "cli.py",
            "search-detail",
            "--department", "文学部",
            "--all-pages",
            "--all-results",
            "--semester", "春学期",
            "--curriculum", "基幹BCD",
            "--report-min", "60",
            "--timeout", "45",
        ]

        with patch.object(sys, "argv", argv), patch("sys.stdout", stdout):
            cli.main()

        _, kwargs = mock_search_and_detail_parallel.call_args
        self.assertEqual(kwargs["department"], "文学部")
        self.assertTrue(kwargs["all_pages"])
        self.assertTrue(kwargs["all_results"])
        self.assertEqual(kwargs["semester_filters"], ["春学期"])
        self.assertEqual(kwargs["curriculum_filters"], ["基幹BCD"])
        self.assertEqual(kwargs["report_min"], 60)
        self.assertEqual(kwargs["timeout_seconds"], 45)
        self.assertTrue(callable(kwargs["progress_callback"]))

    def test_schema_marks_server_side_vs_client_side_filters(self):
        stdout = io.StringIO()

        argv = [
            "cli.py",
            "schema",
        ]

        with patch.object(sys, "argv", argv), patch("sys.stdout", stdout):
            cli.main()

        payload = json.loads(stdout.getvalue())
        search_params = payload["commands"]["search"]["params"]
        self.assertTrue(search_params["keyword"]["server_side"])
        self.assertFalse(search_params["semester"]["server_side"])
        self.assertFalse(search_params["curriculum"]["server_side"])
        self.assertEqual(search_params["year"]["default"], cli.DEFAULT_ACADEMIC_YEAR)
        self.assertEqual(search_params["timeout"]["default"], cli.DEFAULT_MULTI_PAGE_TIMEOUT)


if __name__ == "__main__":
    unittest.main()
