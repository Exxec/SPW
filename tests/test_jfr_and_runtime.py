import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from spw.capture import _wait_for_stable_file, build_attach_start_command
from spw.capture_levels import LEVELS, jfc_path
import subprocess

from spw.jfr_events import (
    event_read_status,
    event_thread_identity,
    event_thread_name,
    parse_iso_duration_seconds,
    read_events_with_status,
    resolve_thread_labels,
    stack_class_names,
    top_frame,
)
from spw.models import FingerprintResult
from spw.runtime_capability import detect_runtime_capabilities

# A trimmed, real jdk.ExecutionSample event shape, taken from an actual
# `jfr print --json` run against a JDK 25 recording (see the session notes
# for how it was captured) rather than guessed at.
REAL_EXECUTION_SAMPLE = {
    "type": "jdk.ExecutionSample",
    "values": {
        "startTime": "2026-08-31T09:14:46.847265100-05:00",
        "sampledThread": {"javaName": "pool-1-thread-1", "osName": "pool-1-thread-1", "javaThreadId": 39},
        "stackTrace": {
            "truncated": False,
            "frames": [
                {
                    "method": {"type": {"name": "Load", "classLoader": {"name": "app"}}, "name": "lambda$main$0", "descriptor": "()V"},
                    "lineNumber": 16,
                    "bytecodeIndex": 78,
                    "type": "JIT compiled",
                },
                {
                    "method": {"type": {"name": "java/util/concurrent/ThreadPoolExecutor", "classLoader": {"name": "bootstrap"}}, "name": "runWorker", "descriptor": "()V"},
                    "lineNumber": 1144,
                    "bytecodeIndex": 8,
                    "type": "JIT compiled",
                },
            ],
        },
    },
}

REAL_CPU_LOAD_EVENT = {
    "type": "jdk.CPULoad",
    "values": {"startTime": "2026-08-31T09:14:48.180050400-05:00", "jvmUser": 0.05, "jvmSystem": 0.01, "machineTotal": 0.26},
}


class CaptureLevelsTests(unittest.TestCase):
    def test_all_levels_resolve_to_existing_files(self) -> None:
        for level in LEVELS:
            path = jfc_path(level)
            self.assertTrue(path.is_file(), f"missing bundled .jfc for {level}")

    def test_unknown_level_rejected(self) -> None:
        with self.assertRaises(ValueError):
            jfc_path("NOT_A_LEVEL")

    def test_passive_disables_sampling_and_allocation_events(self) -> None:
        text = jfc_path("PASSIVE").read_text(encoding="utf-8")
        self.assertIn('<event name="jdk.ExecutionSample">', text)
        # The disabled block for ExecutionSample must say false.
        segment = text.split('<event name="jdk.ExecutionSample">')[1].split("</event>")[0]
        self.assertIn("false", segment)


class CaptureQuotingTests(unittest.TestCase):
    def test_attach_start_command_quotes_paths_for_jcmd_retokenization(self) -> None:
        jcmd = Path("C:/Program Files/Java/bin/jcmd.exe")
        output = Path("C:/Users/exxec/My Documents/profile.jfr")
        command = build_attach_start_command(jcmd, 123, output, 30, level="STANDARD")
        settings_arg = next(part for part in command if part.startswith("settings="))
        filename_arg = next(part for part in command if part.startswith("filename="))
        self.assertTrue(settings_arg.startswith('settings="') and settings_arg.endswith('"'))
        self.assertTrue(filename_arg.startswith('filename="') and filename_arg.endswith('"'))


class WaitForStableFileTests(unittest.TestCase):
    def test_returns_true_for_existing_nonempty_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.jfr"
            path.write_bytes(b"already-complete-data")
            self.assertTrue(_wait_for_stable_file(path, max_wait_seconds=2, poll_interval_seconds=0.1))

    def test_returns_false_when_file_never_appears(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "never.jfr"
            self.assertFalse(_wait_for_stable_file(path, max_wait_seconds=1, poll_interval_seconds=0.2))


class JfrEventsTests(unittest.TestCase):
    def test_parse_iso_duration_seconds(self) -> None:
        self.assertAlmostEqual(parse_iso_duration_seconds("PT0.0000462S"), 0.0000462)
        self.assertAlmostEqual(parse_iso_duration_seconds("PT1.5S"), 1.5)
        self.assertIsNone(parse_iso_duration_seconds(None))
        self.assertIsNone(parse_iso_duration_seconds(""))

    def test_top_frame_and_stack_class_names(self) -> None:
        values = REAL_EXECUTION_SAMPLE["values"]
        frame = top_frame(values)
        self.assertEqual(frame["class_name"], "Load")
        self.assertEqual(frame["method_name"], "lambda$main$0")
        names = stack_class_names(values)
        self.assertEqual(names, ["Load", "java.util.concurrent.ThreadPoolExecutor"])

    def test_event_thread_name_checks_known_keys(self) -> None:
        self.assertEqual(event_thread_name(REAL_EXECUTION_SAMPLE["values"]), "pool-1-thread-1")

    def test_event_thread_identity_extracts_java_thread_id(self) -> None:
        self.assertEqual(event_thread_identity(REAL_EXECUTION_SAMPLE["values"]), ("pool-1-thread-1", 39))

    def test_event_thread_identity_none_without_a_thread_field(self) -> None:
        self.assertIsNone(event_thread_identity({}))

    def test_resolve_thread_labels_disambiguates_only_real_collisions(self) -> None:
        identities = [("pool-1-thread-1", 39), ("pool-1-thread-1", 87), ("worker", 5)]
        labels = resolve_thread_labels(identities)
        # Two distinct java thread ids share the name "pool-1-thread-1" --
        # a real pooled-worker-reuse pattern -- so both must be
        # disambiguated by id, while the uniquely-named "worker" stays plain.
        self.assertEqual(labels[("pool-1-thread-1", 39)], "pool-1-thread-1#39")
        self.assertEqual(labels[("pool-1-thread-1", 87)], "pool-1-thread-1#87")
        self.assertEqual(labels[("worker", 5)], "worker")

    def test_resolve_thread_labels_accepts_a_single_use_generator(self) -> None:
        identities = [("a", 1), ("b", 2)]
        labels = resolve_thread_labels(identity for identity in identities)
        self.assertEqual(labels, {("a", 1): "a", ("b", 2): "b"})

    def test_read_events_with_status_parses_real_shaped_json_and_merges_type(self) -> None:
        fake_stdout = json.dumps({"recording": {"events": [REAL_EXECUTION_SAMPLE, REAL_CPU_LOAD_EVENT]}})
        with patch("spw.jfr_events.subprocess.run") as mock_run:
            mock_run.return_value.stdout = fake_stdout
            mock_run.return_value.returncode = 0
            events, limitation = read_events_with_status(Path("jfr.exe"), Path("test.jfr"), ["jdk.ExecutionSample", "jdk.CPULoad"])
        self.assertIsNone(limitation)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["type"], "jdk.ExecutionSample")
        self.assertEqual(events[1]["jvmUser"], 0.05)
        self.assertEqual(event_read_status(events, limitation), {"status": "OK", "event_count": 2, "reason": None})

    def test_read_events_with_status_returns_empty_list_and_reason_on_failure(self) -> None:
        with patch("spw.jfr_events.subprocess.run", side_effect=OSError("no such tool")):
            events, limitation = read_events_with_status(Path("missing-jfr.exe"), Path("test.jfr"), ["jdk.CPULoad"])
        self.assertEqual(events, [])
        self.assertEqual(limitation, "jfr_print_os_error")
        self.assertEqual(event_read_status(events, limitation), {"status": "READ_FAILED", "event_count": None, "reason": "jfr_print_os_error"})

    def test_event_read_status_distinguishes_genuine_zero_from_read_failure(self) -> None:
        self.assertEqual(event_read_status([], None), {"status": "OK", "event_count": 0, "reason": None})
        self.assertEqual(event_read_status([], "jfr_print_timed_out"), {"status": "READ_FAILED", "event_count": None, "reason": "jfr_print_timed_out"})

    def test_read_events_with_status_distinguishes_failure_reasons(self) -> None:
        cases = [
            (FileNotFoundError("no such file"), "jfr_tool_not_found"),
            (subprocess.TimeoutExpired(cmd="jfr", timeout=180), "jfr_print_timed_out"),
            (subprocess.CalledProcessError(returncode=3, cmd="jfr"), "jfr_print_failed:3"),
            (PermissionError("denied"), "jfr_print_os_error"),
        ]
        for exception, expected_limitation in cases:
            with patch("spw.jfr_events.subprocess.run", side_effect=exception):
                events, limitation = read_events_with_status(Path("jfr.exe"), Path("test.jfr"), ["jdk.CPULoad"])
            self.assertEqual(events, [])
            self.assertEqual(limitation, expected_limitation)

    def test_read_events_with_status_reports_unparseable_output(self) -> None:
        with patch("spw.jfr_events.subprocess.run") as mock_run:
            mock_run.return_value.stdout = "not json"
            mock_run.return_value.returncode = 0
            events, limitation = read_events_with_status(Path("jfr.exe"), Path("test.jfr"), ["jdk.CPULoad"])
        self.assertEqual(events, [])
        self.assertEqual(limitation, "jfr_print_output_unparseable")

    def test_read_events_with_status_none_on_genuine_success(self) -> None:
        with patch("spw.jfr_events.subprocess.run") as mock_run:
            mock_run.return_value.stdout = json.dumps({"recording": {"events": []}})
            mock_run.return_value.returncode = 0
            events, limitation = read_events_with_status(Path("jfr.exe"), Path("test.jfr"), ["jdk.CPULoad"])
        self.assertEqual(events, [])
        self.assertIsNone(limitation)


class RuntimeCapabilityTests(unittest.TestCase):
    def test_base_jdk_labels(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            vanilla = detect_runtime_capabilities(root, {"java_version": "17.0.9", "implementor": "Eclipse Adoptium", "source": "release-file"}, {})
            by_id = {c["id"]: c for c in vanilla}
            self.assertEqual(by_id["VanillaJava17"]["state"], "ENABLED")
            self.assertEqual(by_id["GenericAlternateJDK"]["state"], "NOT_DETECTED")

            alternate = detect_runtime_capabilities(root, {"java_version": "21.0.1", "implementor": "Eclipse Adoptium", "source": "release-file"}, {})
            by_id = {c["id"]: c for c in alternate}
            self.assertEqual(by_id["GenericAlternateJDK"]["state"], "ENABLED")
            self.assertEqual(by_id["VanillaJava17"]["state"], "NOT_DETECTED")

            unavailable = detect_runtime_capabilities(root, {"source": "UNAVAILABLE"}, {})
            by_id = {c["id"]: c for c in unavailable}
            self.assertEqual(by_id["VanillaJava17"]["state"], "UNKNOWN")

    def test_mikohime_and_fast_rendering_markers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Miko_Info.txt").write_text("Fast Rendering: enabled\nMemory: 4096\n", encoding="utf-8")
            (root / "fr.bat").write_text("", encoding="utf-8")
            (root / "fr.vmparams").write_text("", encoding="utf-8")
            capabilities = detect_runtime_capabilities(root, {"source": "UNAVAILABLE"}, {})
            by_id = {c["id"]: c for c in capabilities}
            self.assertEqual(by_id["MikohimeConfiguration"]["state"], "ENABLED")
            self.assertTrue(by_id["MikohimeConfiguration"]["parsed_configuration"]["fast_rendering"])
            self.assertEqual(by_id["FastRendering"]["state"], "ENABLED")

    def test_resource_cache_and_prepatcher_are_low_confidence_pattern_matches(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "resource-cache").mkdir()
            (root / "prepatcher.jar").write_text("", encoding="utf-8")
            capabilities = detect_runtime_capabilities(root, {"source": "UNAVAILABLE"}, {})
            by_id = {c["id"]: c for c in capabilities}
            self.assertEqual(by_id["FastRenderingResourceCache"]["state"], "ENABLED")
            self.assertTrue(by_id["FastRenderingResourceCache"]["unsupported_assumptions"])
            self.assertEqual(by_id["StarsectorPrepatcher"]["state"], "ENABLED")

    def test_unknown_modified_runtime_from_core_integrity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            core_integrity = {"file_status": {"starsector.exe": "UNKNOWN_DIFFERENCE"}}
            capabilities = detect_runtime_capabilities(root, {"source": "UNAVAILABLE"}, core_integrity)
            by_id = {c["id"]: c for c in capabilities}
            self.assertEqual(by_id["UnknownModifiedRuntime"]["state"], "ENABLED")
            self.assertIn("starsector.exe", by_id["UnknownModifiedRuntime"]["evidence"])


if __name__ == "__main__":
    unittest.main()
