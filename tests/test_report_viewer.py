import json
import tempfile
import unittest
from pathlib import Path

from spw.report_viewer import render_html_report


class ReportViewerTests(unittest.TestCase):
    def test_renders_from_flat_inventory_layout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "environment.json").write_text(json.dumps({"installation_path": str(root), "java": {"implementor": "Eclipse Adoptium", "java_version": "17"}, "mod_count": 3, "findings": []}), encoding="utf-8")
            html = render_html_report(root)
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("Eclipse Adoptium", html)
        self.assertIn("3", html)

    def test_renders_from_nested_diagnose_layout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "inventory").mkdir()
            (root / "inventory" / "environment.json").write_text(json.dumps({"installation_path": str(root), "java": {}, "mod_count": 7, "findings": []}), encoding="utf-8")
            (root / "analysis").mkdir()
            (root / "analysis" / "tick-analysis.json").write_text(json.dumps({"ticks_available": True, "tick_count": 10, "average_tick_seconds": 0.02, "stalls": []}), encoding="utf-8")
            html = render_html_report(root)
        self.assertIn("7", html)
        self.assertIn("Ticks observed: 10", html)

    def test_missing_optional_sections_do_not_crash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            html = render_html_report(Path(directory))
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("UNKNOWN", html)

    def test_mod_derived_strings_are_html_escaped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            malicious_finding = {"id": "x", "category": "mod-inventory", "severity": "high", "confidence": "DETERMINISTIC", "explanation": "<script>alert(1)</script>", "file": None, "evidence": []}
            (root / "environment.json").write_text(json.dumps({"installation_path": str(root), "java": {}, "mod_count": 1, "findings": [malicious_finding]}), encoding="utf-8")
            html = render_html_report(root)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_ticks_not_available_renders_explanatory_message(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tick-analysis.json").write_text(json.dumps({"ticks_available": False}), encoding="utf-8")
            html = render_html_report(root)
        self.assertIn("Not available", html)


if __name__ == "__main__":
    unittest.main()
