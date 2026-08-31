import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from spw.capture import run_attach_capture
from spw.models import FingerprintResult

TOOLING_JDK_25 = {
    "executable": "C:/jdk25/bin/java.exe",
    "implementor": "Eclipse Adoptium",
    "implementor_version": "Temurin-25.0.4.1+1",
    "java_runtime_version": "25.0.4.1+1-LTS",
    "java_version": "25.0.4.1",
    "jvm_variant": "UNKNOWN",
    "os_arch": "amd64",
    "os_name": "Windows",
    "image_type": "JDK",
    "source": "release-file",
}

# Matches a real Temurin-27-beta+22-ea build found inside a real
# Starsector installation's `jdk-27+22` directory -- confirmed end-to-end
# via `spw capture --attach-pid` against that real process.
TARGET_JVM_27_EA = {
    "pid": 4242,
    "vm_name": "OpenJDK 64-Bit Server VM",
    "vm_version": "27-beta+22-ea",
    "jdk_version": "27.0.0",
    "source": "jcmd-VM.version",
    "limitation": None,
}

TARGET_JVM_25 = {
    "pid": 4242,
    "vm_name": "OpenJDK 64-Bit Server VM",
    "vm_version": "25.0.4.1+1-LTS",
    "jdk_version": "25.0.4.1",
    "source": "jcmd-VM.version",
    "limitation": None,
}


class RunAttachCaptureTargetJvmTests(unittest.TestCase):
    def _run(self, target_jvm: dict) -> tuple[dict, FingerprintResult]:
        result = FingerprintResult(installation_path=Path("."))
        with tempfile.TemporaryDirectory() as tmpdir:
            output_file = Path(tmpdir) / "profile.jfr"
            with (
                patch("spw.capture._jcmd_path", return_value=Path("jdk25/bin/jcmd.exe")),
                patch("spw.capture.detect_java", return_value=TOOLING_JDK_25),
                patch("spw.capture.detect_target_jvm", return_value=target_jvm),
                patch("spw.capture.subprocess.run"),
                patch("spw.capture._wait_for_stable_file", return_value=True),
            ):
                output_file.write_bytes(b"fake")
                descriptor = run_attach_capture(result, pid=4242, output_file=output_file, duration_seconds=1)
        return descriptor, result

    def test_descriptor_carries_both_tooling_jdk_and_target_jvm_distinctly(self) -> None:
        descriptor, _ = self._run(TARGET_JVM_25)
        self.assertEqual(descriptor["tooling_jdk"], TOOLING_JDK_25)
        self.assertEqual(descriptor["target_jvm"], TARGET_JVM_25)

    def test_mismatched_major_versions_add_an_informational_finding(self) -> None:
        # The exact scenario the user described: profiler tooling on
        # Temurin 25, but the target Starsector process actually running
        # under a Mikohime-managed Java 27 (early-access) build.
        _, result = self._run(TARGET_JVM_27_EA)
        finding_ids = [f.id for f in result.findings]
        self.assertIn("tooling-jdk-target-jvm-version-mismatch", finding_ids)
        mismatch = next(f for f in result.findings if f.id == "tooling-jdk-target-jvm-version-mismatch")
        self.assertEqual(mismatch.severity, "info")
        self.assertIn("25", mismatch.explanation)
        self.assertIn("27", mismatch.explanation)

    def test_matching_major_versions_add_no_mismatch_finding(self) -> None:
        _, result = self._run(TARGET_JVM_25)
        finding_ids = [f.id for f in result.findings]
        self.assertNotIn("tooling-jdk-target-jvm-version-mismatch", finding_ids)


class RunAttachCaptureJfrRejectedButExitsZeroTests(unittest.TestCase):
    def test_jcmd_stdout_is_surfaced_when_jfr_start_is_rejected_but_exits_zero(self) -> None:
        # Confirmed directly against a real JRE 8 process with JFR not
        # commercially unlocked: `jcmd <pid> JFR.start` prints "Java Flight
        # Recorder not enabled. Use VM.unlock_commercial_features to
        # enable." to stdout but exits 0, so `check=True` never raises and
        # the only other signal is the recording file never appearing.
        # That real jcmd output must end up in incomplete_reason, not just
        # the generic "file did not appear" message.
        result = FingerprintResult(installation_path=Path("."))
        fake_completed = Mock(stdout="Java Flight Recorder not enabled.\n\nUse VM.unlock_commercial_features to enable.", returncode=0)
        with tempfile.TemporaryDirectory() as tmpdir:
            output_file = Path(tmpdir) / "profile.jfr"
            with (
                patch("spw.capture._jcmd_path", return_value=Path("jdk8/bin/jcmd.exe")),
                patch("spw.capture.detect_java", return_value=TOOLING_JDK_25),
                patch("spw.capture.detect_target_jvm", return_value=TARGET_JVM_25),
                patch("spw.capture.subprocess.run", return_value=fake_completed),
                patch("spw.capture._wait_for_stable_file", return_value=False),
                patch("spw.capture.time.sleep"),
            ):
                descriptor = run_attach_capture(result, pid=4242, output_file=output_file, duration_seconds=1)
        self.assertIsNotNone(descriptor["incomplete_reason"])
        self.assertIn("Java Flight Recorder not enabled", descriptor["incomplete_reason"])
        self.assertIsNone(descriptor["output_path"])
