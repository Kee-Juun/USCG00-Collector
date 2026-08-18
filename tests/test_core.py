from __future__ import annotations

import csv
import logging
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

from core.collector import resolve_output_root
from core.models import CollectionResult, DecisionRow
from core.source import USCGSource
from download.pdf_processor import classify_order_document, extract_decision_date
from reporting.csv_reporter import write_reports
from ui.main_window import CollectorWindow, friendly_status
from utils.sfview_checker import extract_filename_date, normalize_docket


class CoreTests(unittest.TestCase):
    def test_year_specific_source_url_override(self):
        source = USCGSource({
            "user_agent": "test",
            "source_url_template": "https://example.test/ALJ-Decisions-{year}/",
            "source_year_urls": {"2025": "https://example.test/ALJ-2025-Decision/"},
        })
        self.assertEqual(source.year_url(2025), "https://example.test/ALJ-2025-Decision/")
        self.assertEqual(source.year_url(2026), "https://example.test/ALJ-Decisions-2026/")

    def test_filename_date(self):
        self.assertEqual(extract_filename_date("LDC_SMD_2026-0002_03032026.pdf"), date(2026, 3, 3))

    def test_docket_normalization(self):
        self.assertEqual(normalize_docket("LDC_SMD_2026-0024_01302026.pdf"), "2026-0024")

    def test_last_page_context_wins(self):
        pages = ["Filed 01/01/2026 for docket 2026-0002", "DONE AND DATED March 3, 2026"]
        self.assertEqual(extract_decision_date(pages, "2026-0002"), date(2026, 3, 3))

    def test_ordinal_day_format(self):
        self.assertEqual(extract_decision_date(["Ordered this 15th day of January, 2026"], "2026-0007"), date(2026, 1, 15))

    def test_accepts_all_common_order_types(self):
        examples = [
            "CONSENT ORDER\nIT IS HEREBY ORDERED that the agreement is approved.",
            "ADMISSION ORDER\nWHEREFORE, ORDER",
            "DEFAULT ORDER\nIT IS FURTHER ORDERED that the credential is revoked.",
            "ORDER OF WITHDRAWAL\nThe complaint is withdrawn.",
            "DECISION AND FINAL ORDER\nThe decision is affirmed.",
            "DECISION OF THE COMMANDANT\nAppeal No. 2057",
        ]
        for text in examples:
            with self.subTest(text=text.splitlines()[0]):
                self.assertTrue(classify_order_document(text)[0])

    def test_excludes_annex_and_manual_correction(self):
        text = "EXECUTIVE ORDER\nAMENDMENTS TO THE MANUAL FOR COURTS-MARTIAL\nANNEX\nSection 1 is amended"
        accepted, reason = classify_order_document(text)
        self.assertFalse(accepted)
        self.assertIn("annex", reason.lower())

    def test_excludes_non_order_filing(self):
        accepted, reason = classify_order_document("MOTION FOR APPROVAL OF SETTLEMENT AGREEMENT\nThe parties jointly move")
        self.assertFalse(accepted)
        self.assertIn("non-order", reason.lower())

    def test_report_has_copyable_final_filename(self):
        result = CollectionResult(
            docket="2026-0002",
            status="SAVED",
            message="Saved",
            output_file=r"C:\\Collected\\LDC_SMD_2026-0002_03032026.pdf",
            final_filename="LDC_SMD_2026-0002_03032026.pdf",
        )
        self.assertEqual(result.as_dict()["final_filename"], "LDC_SMD_2026-0002_03032026.pdf")

    def test_reports_use_human_written_headers(self):
        result = CollectionResult(
            docket="2026-0002",
            status="SAVED",
            message="Saved",
            final_filename="LDC_SMD_2026-0002_03032026.pdf",
        )
        temp_dir = Path.cwd() / "tests" / "_generated_report"
        temp_dir.mkdir(exist_ok=True)
        try:
            paths = write_reports(temp_dir, [result])
            with paths["csv"].open(encoding="utf-8-sig", newline="") as handle:
                headers = next(csv.reader(handle))
            generated_names = {path.name for path in temp_dir.iterdir()}
        finally:
            for path in temp_dir.iterdir():
                path.unlink()
            temp_dir.rmdir()
        self.assertIn("Docket Number", headers)
        self.assertIn("Final Filename", headers)
        self.assertNotIn("Output File", headers)
        self.assertNotIn("final_filename", headers)
        self.assertEqual(set(paths), {"csv"})
        self.assertEqual(generated_names, {f"Report_{temp_dir.name}.csv"})

    def test_relative_output_root_resolves_under_home(self):
        expected = Path.home() / "Downloads" / "USCG00 Collections"
        self.assertEqual(resolve_output_root({"output_root": "Downloads/USCG00 Collections"}), expected)

    def test_duplicate_result_does_not_create_collected_folder(self):
        from config.settings import DEFAULTS
        from core.collector import Collector

        collector = Collector(dict(DEFAULTS), logging.getLogger("test-empty-collected"))
        collector.sfview.matches = Mock(return_value=[Mock(label="existing.pdf")])
        row = DecisionRow(docket="2026-0002", pdf_url="https://example.test/decision.pdf")
        with tempfile.TemporaryDirectory() as temporary:
            collected_dir = Path(temporary) / "Collected"
            result = collector._process(row, collected_dir)
            self.assertEqual(result.status, "SKIPPED_SFVIEW")
            self.assertFalse(collected_dir.exists())
        collector.source.close()

    def test_ui_filters_detailed_logs_to_relevant_statuses(self):
        self.assertEqual(
            friendly_status("SF-View index ready: 884 files"),
            "Duplicate check ready. Loading USCG decisions…",
        )
        self.assertEqual(friendly_status("[SAVED] 2026-0002 - Saved file.pdf"), "")

    def test_result_dialog_ok_opens_folder_and_resets(self):
        host = Mock()
        ok_button = object()
        with patch("ui.main_window.QMessageBox") as message_box:
            dialog = message_box.return_value
            dialog.addButton.return_value = ok_button
            dialog.clickedButton.return_value = ok_button
            CollectorWindow._show_result_dialog(host, None, "Complete", "Saved")
        host._open_latest_run.assert_called_once_with()
        host._reset_for_next_run.assert_called_once_with()

    def test_result_dialog_dismissal_does_not_open_folder(self):
        host = Mock()
        with patch("ui.main_window.QMessageBox") as message_box:
            dialog = message_box.return_value
            dialog.addButton.return_value = object()
            dialog.clickedButton.return_value = None
            CollectorWindow._show_result_dialog(host, None, "Error", "Failed")
        host._open_latest_run.assert_not_called()
        host._reset_for_next_run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
