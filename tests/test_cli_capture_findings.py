import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from spw.cli import main as cli_main
from spw.models import FingerprintResult


def _fake_run_attach_capture_with_finding(result: FingerprintResult, pid: int, output_file: Path, duration_seconds: int, **kwargs) -> dict:
    """Stand-in for `run_attach_capture` that raises a finding, exactly as the
    real tooling-jdk-target-jvm-version-mismatch finding does, without
    needing a real jcmd/JFR-capable JVM in a unit test."""

    result.add(
        id="tooling-jdk-target-jvm-version-mismatch",
        category="capture",
        severity="info",
        confidence="DETERMINISTIC",
        explanation="The tooling JDK used to attach (major version 25) differs from the target JVM (major version 27).",
    )
    output_file.write_bytes(b"fake-jfr-bytes")
    return {
        "capture_type": "attach",
        "target": f"pid:{pid}",
        "level": "PASSIVE",
        "settings": {"duration_seconds": duration_seconds, "output_file": str(output_file)},
        "output_path": str(output_file),
        "incomplete_reason": None,
        "tooling_jdk": {"implementor": "Eclipse Adoptium", "java_version": "25.0.4"},
        "target_jvm": {"vm_name": "OpenJDK 64-Bit Server VM", "jdk_version": "27.0.0"},
    }


class CliCaptureFindingsTests(unittest.TestCase):
    def test_findings_raised_during_standalone_capture_are_not_silently_discarded(self) -> None:
        # Before this fix, `spw capture` (unlike `spw diagnose`) had no
        # inventory stage to fold FingerprintResult.findings into, so any
        # finding raised during capture -- jcmd-unavailable,
        # jfr-start-failed, or the tooling-jdk-target-jvm-version-mismatch
        # finding added this session -- vanished without a trace.
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "artifacts"
            with patch("spw.cli.run_attach_capture", side_effect=_fake_run_attach_capture_with_finding):
                exit_code = cli_main(["capture", "--attach-pid", "1234", "--output", str(output)])
            self.assertEqual(exit_code, 0)

            findings_path = output / "findings.json"
            self.assertTrue(findings_path.is_file())
            findings = json.loads(findings_path.read_text(encoding="utf-8"))["findings"]
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0]["id"], "tooling-jdk-target-jvm-version-mismatch")

            report_text = (output / "CAPTURE_REPORT.md").read_text(encoding="utf-8")
            self.assertIn("tooling-jdk-target-jvm-version-mismatch", report_text)
            self.assertIn("## Findings", report_text)

    def test_no_findings_section_still_says_so_explicitly(self) -> None:
        def _fake_no_findings(result: FingerprintResult, pid: int, output_file: Path, duration_seconds: int, **kwargs) -> dict:
            output_file.write_bytes(b"fake-jfr-bytes")
            return {
                "capture_type": "attach",
                "target": f"pid:{pid}",
                "level": "PASSIVE",
                "settings": {},
                "output_path": str(output_file),
                "incomplete_reason": None,
                "tooling_jdk": None,
                "target_jvm": None,
            }

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "artifacts"
            with patch("spw.cli.run_attach_capture", side_effect=_fake_no_findings):
                exit_code = cli_main(["capture", "--attach-pid", "1234", "--output", str(output)])
            self.assertEqual(exit_code, 0)
            findings = json.loads((output / "findings.json").read_text(encoding="utf-8"))["findings"]
            self.assertEqual(findings, [])
            self.assertIn("No findings.", (output / "CAPTURE_REPORT.md").read_text(encoding="utf-8"))
