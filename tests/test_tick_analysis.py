import unittest
from pathlib import Path
from unittest.mock import patch

from spw.tick_analysis import analyze_ticks, correlate_stalls_with_execution_samples

# Field names grounded in a real captured com.spw.TickBoundary event (see
# session notes): type, tickIndex, elapsedSeconds, startTime.
NORMAL_TICKS = [
    {"type": "com.spw.TickBoundary", "tickIndex": i, "elapsedSeconds": 0.0166, "startTime": f"2026-01-01T00:00:{i:02d}.000-00:00"}
    for i in range(5)
]


class TickAnalysisTests(unittest.TestCase):
    def test_unavailable_when_no_events(self) -> None:
        with patch("spw.tick_analysis.read_events_with_status", return_value=([], None)):
            result = analyze_ticks(Path("jfr.exe"), Path("test.jfr"))
        self.assertFalse(result["ticks_available"])
        self.assertEqual(result["tick_count"], 0)
        self.assertIsNone(result["read_limitation"])

    def test_normal_ticks_have_no_stalls(self) -> None:
        with patch("spw.tick_analysis.read_events_with_status", return_value=(list(NORMAL_TICKS), None)):
            result = analyze_ticks(Path("jfr.exe"), Path("test.jfr"))
        self.assertTrue(result["ticks_available"])
        self.assertEqual(result["tick_count"], 5)
        self.assertAlmostEqual(result["average_tick_seconds"], 0.0166)
        self.assertEqual(result["stalls"], [])

    def test_stall_detected_when_one_tick_is_much_longer(self) -> None:
        events = list(NORMAL_TICKS) + [{"type": "com.spw.TickBoundary", "tickIndex": 5, "elapsedSeconds": 2.0, "startTime": "2026-01-01T00:00:05.000-00:00"}]
        with patch("spw.tick_analysis.read_events_with_status", return_value=(events, None)):
            result = analyze_ticks(Path("jfr.exe"), Path("test.jfr"))
        self.assertEqual(len(result["stalls"]), 1)
        self.assertEqual(result["stalls"][0]["tick_index"], 5)

    def test_read_failure_is_distinguished_from_genuine_absence(self) -> None:
        with patch("spw.tick_analysis.read_events_with_status", return_value=([], "jfr_print_timed_out")):
            result = analyze_ticks(Path("jfr.exe"), Path("test.jfr"))
        self.assertFalse(result["ticks_available"])
        self.assertEqual(result["read_limitation"], "jfr_print_timed_out")
        self.assertIn("jfr_print_timed_out", result["limitations"])


def _exec_sample(start_time: str, class_name: str, method_name: str) -> dict:
    return {
        "type": "jdk.ExecutionSample",
        "startTime": start_time,
        "stackTrace": {"frames": [{"method": {"type": {"name": class_name}, "name": method_name}, "lineNumber": 1, "type": "JIT compiled"}]},
    }


class StallCorrelationTests(unittest.TestCase):
    def test_no_correlation_without_stalls_or_samples(self) -> None:
        self.assertEqual(correlate_stalls_with_execution_samples({"stalls": []}, [_exec_sample("2026-01-01T00:00:09.000+00:00", "A", "m")]), [])
        self.assertEqual(correlate_stalls_with_execution_samples({"stalls": [{"tick_index": 1, "start_time": "2026-01-01T00:00:10.000+00:00", "elapsed_seconds": 2.0}]}, []), [])

    def test_samples_inside_the_stall_window_are_counted_outside_are_not(self) -> None:
        tick_result = {"stalls": [{"tick_index": 7, "start_time": "2026-01-01T00:00:10.000+00:00", "elapsed_seconds": 2.0}]}
        samples = [
            _exec_sample("2026-01-01T00:00:09.000+00:00", "com/example/Hot", "tick"),  # inside [08:00, 10:00]
            _exec_sample("2026-01-01T00:00:09.500+00:00", "com/example/Hot", "tick"),  # inside
            _exec_sample("2026-01-01T00:00:05.000+00:00", "com/example/Cold", "idle"),  # outside, before window
            _exec_sample("2026-01-01T00:00:15.000+00:00", "com/example/Cold", "idle"),  # outside, after window
        ]
        correlated = correlate_stalls_with_execution_samples(tick_result, samples)
        self.assertEqual(len(correlated), 1)
        entry = correlated[0]
        self.assertEqual(entry["tick_index"], 7)
        self.assertEqual(entry["execution_samples_in_window"], 2)
        self.assertEqual(entry["top_frames"][0]["class_name"], "com.example.Hot")
        self.assertEqual(entry["top_frames"][0]["samples"], 2)

    def test_stall_with_no_samples_in_window_reports_zero_not_missing(self) -> None:
        tick_result = {"stalls": [{"tick_index": 3, "start_time": "2026-01-01T00:00:10.000+00:00", "elapsed_seconds": 1.0}]}
        samples = [_exec_sample("2026-01-01T00:00:00.000+00:00", "com/example/Cold", "idle")]
        correlated = correlate_stalls_with_execution_samples(tick_result, samples)
        self.assertEqual(correlated[0]["execution_samples_in_window"], 0)
        self.assertEqual(correlated[0]["top_frames"], [])


if __name__ == "__main__":
    unittest.main()
