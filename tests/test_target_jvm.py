import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from spw.target_jvm import detect_target_jvm, parse_vm_version_output

# Real `jcmd <pid> VM.version` stdout, captured on this machine against a
# running Temurin 25.0.4.1 JVM -- queried via a *different* Temurin
# 25.0.4.7 install's jcmd, confirming the report describes the target
# process, not whichever jcmd binary was used to ask it.
REAL_VM_VERSION_OUTPUT = "32800:\nOpenJDK 64-Bit Server VM version 25.0.4.1+1-LTS\nJDK 25.0.4.1\n"

# Real `jcmd <pid> VM.version` stdout, captured against a genuine
# Temurin-27-beta+22-ea build found inside a real Starsector installation's
# `jdk-27+22` directory (a real Mikohime-style alternate-JDK setup, the
# exact scenario this module exists for) -- queried, as above, via a
# different (Temurin 25) install's jcmd.
REAL_EARLY_ACCESS_VM_VERSION_OUTPUT = "15964:\nOpenJDK 64-Bit Server VM version 27-beta+22-ea\nJDK 27.0.0\n"


class ParseVmVersionOutputTests(unittest.TestCase):
    def test_parses_real_captured_output(self) -> None:
        result = parse_vm_version_output(REAL_VM_VERSION_OUTPUT)
        self.assertEqual(result["vm_name"], "OpenJDK 64-Bit Server VM")
        self.assertEqual(result["vm_version"], "25.0.4.1+1-LTS")
        self.assertEqual(result["jdk_version"], "25.0.4.1")

    def test_parses_real_captured_early_access_output(self) -> None:
        # Real early-access build, real output -- not a guessed format.
        # Note the real shape differs slightly from the pre-validation
        # assumption: the VM-version line keeps the "-beta+22-ea" suffix,
        # but the JDK line itself is a plain "27.0.0", not "27-ea".
        result = parse_vm_version_output(REAL_EARLY_ACCESS_VM_VERSION_OUTPUT)
        self.assertEqual(result["vm_version"], "27-beta+22-ea")
        self.assertEqual(result["jdk_version"], "27.0.0")

    def test_unrecognized_output_leaves_fields_none_rather_than_guessing(self) -> None:
        result = parse_vm_version_output("some vendor's completely different format\n")
        self.assertIsNone(result["vm_name"])
        self.assertIsNone(result["vm_version"])
        self.assertIsNone(result["jdk_version"])


class DetectTargetJvmTests(unittest.TestCase):
    def test_success_reports_target_not_tool(self) -> None:
        with patch("spw.target_jvm.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = REAL_VM_VERSION_OUTPUT
            mock_run.return_value.stderr = ""
            result = detect_target_jvm(Path("some-other-jdk/bin/jcmd.exe"), pid=32800)
        self.assertEqual(result["source"], "jcmd-VM.version")
        self.assertEqual(result["jdk_version"], "25.0.4.1")
        self.assertIsNone(result["limitation"])
        mock_run.assert_called_once_with(
            [str(Path("some-other-jdk/bin/jcmd.exe")), "32800", "VM.version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

    def test_nonzero_returncode_is_a_limitation_not_a_crash(self) -> None:
        with patch("spw.target_jvm.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            mock_run.return_value.stdout = ""
            mock_run.return_value.stderr = "32800: No such process"
            result = detect_target_jvm(Path("jcmd.exe"), pid=32800)
        self.assertEqual(result["source"], "UNAVAILABLE")
        self.assertIn("No such process", result["limitation"])
        self.assertIsNone(result["jdk_version"])

    def test_timeout_is_a_limitation_not_a_crash(self) -> None:
        with patch("spw.target_jvm.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="jcmd", timeout=10)):
            result = detect_target_jvm(Path("jcmd.exe"), pid=1)
        self.assertEqual(result["source"], "UNAVAILABLE")
        self.assertIsNotNone(result["limitation"])
