import unittest
from pathlib import Path
from unittest.mock import patch

from spw.allocation_gc_analysis import analyze_allocation_and_gc
from spw.attribution import attribute_execution_samples, resolve_class_attribution
from spw.cpu_thread_analysis import analyze_cpu_and_threads
from spw.startup_analysis import analyze_startup

EXEC_SAMPLE = {
    "type": "jdk.ExecutionSample",
    "sampledThread": {"javaName": "worker-1"},
    "stackTrace": {
        "frames": [
            {"method": {"type": {"name": "com/example/somemod/Hot"}, "name": "tick"}, "lineNumber": 1, "type": "JIT compiled"},
            {"method": {"type": {"name": "java/lang/Thread"}, "name": "run"}, "lineNumber": 2, "type": "Interpreted"},
        ]
    },
}
RUNTIME_ONLY_SAMPLE = {
    "type": "jdk.ExecutionSample",
    "sampledThread": {"javaName": "worker-2"},
    "stackTrace": {"frames": [{"method": {"type": {"name": "java/util/HashMap"}, "name": "get"}, "lineNumber": 3, "type": "Interpreted"}]},
}
THREAD_CPU = {"type": "jdk.ThreadCPULoad", "eventThread": {"javaName": "worker-1"}, "user": 0.4, "system": 0.1}
THREAD_START = {"type": "jdk.ThreadStart", "thread": {"javaName": "worker-1"}}
THREAD_END = {"type": "jdk.ThreadEnd", "thread": {"javaName": "worker-1"}}
CPU_LOAD = {"type": "jdk.CPULoad", "jvmUser": 0.3, "jvmSystem": 0.05, "machineTotal": 0.5}

ALLOC_SAMPLE = {
    "type": "jdk.ObjectAllocationSample",
    "objectClass": {"name": "[B"},
    "weight": 1024,
    "stackTrace": {"frames": [{"method": {"type": {"name": "com/example/somemod/Hot"}, "name": "tick"}, "lineNumber": 1, "type": "JIT compiled"}]},
}
GC_PAUSE = {"type": "jdk.GCPhasePause", "duration": "PT0.002S", "gcId": 0}
HEAP_BEFORE = {"type": "jdk.GCHeapSummary", "gcId": 0, "when": "Before GC", "heapUsed": 1000}
HEAP_AFTER = {"type": "jdk.GCHeapSummary", "gcId": 0, "when": "After GC", "heapUsed": 400}

CLASS_LOADING_1 = {"type": "jdk.ClassLoadingStatistics", "startTime": "2026-01-01T00:00:01.000-00:00", "loadedClassCount": 100, "unloadedClassCount": 0}
CLASS_LOADING_2 = {"type": "jdk.ClassLoadingStatistics", "startTime": "2026-01-01T00:00:02.000-00:00", "loadedClassCount": 150, "unloadedClassCount": 0}
EARLY_EXEC_SAMPLE = {"type": "jdk.ExecutionSample", "startTime": "2026-01-01T00:00:00.500-00:00", "sampledThread": {"javaName": "worker"}, "stackTrace": {"frames": []}}


def _fake_read_events(event_map):
    def _read(jfr_tool, recording_path, event_types):
        results = []
        for event_type in event_types:
            results.extend(event_map.get(event_type, []))
        return results, None

    return _read


class AttributionTests(unittest.TestCase):
    def test_exact_class_index_hit(self) -> None:
        result = resolve_class_attribution("com.example.somemod.Hot", {"com/example/somemod/Hot": ["somemod"]}, {})
        self.assertEqual(result["confidence"], "EXACT")
        self.assertEqual(result["owner_candidates"], ["somemod"])

    def test_ambiguous_shared_class(self) -> None:
        result = resolve_class_attribution("shared.Lib", {"shared/Lib": ["modA", "modB"]}, {})
        self.assertEqual(result["confidence"], "AMBIGUOUS")

    def test_package_prefix_fallback(self) -> None:
        result = resolve_class_attribution("com.example.somemod.Deep.Nested", {}, {"com/example": ["somemod"]})
        self.assertEqual(result["confidence"], "LIKELY")
        self.assertEqual(result["attribution_path"], "package prefix")

    def test_runtime_class_is_unowned_not_unknown(self) -> None:
        result = resolve_class_attribution("java.util.HashMap", {}, {})
        self.assertEqual(result["confidence"], "UNOWNED")

    def test_unrecognized_non_runtime_class_is_unknown(self) -> None:
        result = resolve_class_attribution("com.mystery.Thing", {}, {})
        self.assertEqual(result["confidence"], "UNKNOWN")

    def test_attribute_execution_samples_skips_runtime_frames(self) -> None:
        aggregate = attribute_execution_samples([EXEC_SAMPLE, RUNTIME_ONLY_SAMPLE], {"com/example/somemod/Hot": ["somemod"]}, {})
        self.assertEqual(aggregate["total_samples_attributed"], 2)
        self.assertEqual(aggregate["samples_by_owner"]["somemod"], 1)
        self.assertEqual(aggregate["samples_by_owner"]["UNOWNED"], 1)


class CpuThreadAnalysisTests(unittest.TestCase):
    def test_analyze_cpu_and_threads(self) -> None:
        event_map = {
            "jdk.ExecutionSample": [EXEC_SAMPLE],
            "jdk.ThreadCPULoad": [THREAD_CPU],
            "jdk.ThreadStart": [THREAD_START],
            "jdk.ThreadEnd": [],
            "jdk.CPULoad": [CPU_LOAD],
        }
        with patch("spw.cpu_thread_analysis.read_events_with_status", side_effect=_fake_read_events(event_map)):
            result = analyze_cpu_and_threads(Path("jfr.exe"), Path("test.jfr"))
        self.assertTrue(result["execution_samples_available"])
        self.assertEqual(result["total_execution_samples"], 1)
        self.assertEqual(result["samples_by_thread"]["worker-1"], 1)
        self.assertIn(("worker-1"), result["thread_lifecycle"]["still_running_at_capture_end"])
        self.assertAlmostEqual(result["average_jvm_cpu"]["user"], 0.3)

    def test_reused_thread_name_does_not_hide_a_still_running_thread(self) -> None:
        # Thread id 1 named "worker" started and ended; a *different*
        # thread, id 2, reusing the same name "worker", started but never
        # ended. Name-only tracking would compute {"worker"} - {"worker"}
        # = {} and hide the still-running one; identity-based tracking
        # must not.
        event_map = {
            "jdk.ExecutionSample": [],
            "jdk.ThreadCPULoad": [],
            "jdk.ThreadStart": [
                {"type": "jdk.ThreadStart", "thread": {"javaName": "worker", "javaThreadId": 1}},
                {"type": "jdk.ThreadStart", "thread": {"javaName": "worker", "javaThreadId": 2}},
            ],
            "jdk.ThreadEnd": [
                {"type": "jdk.ThreadEnd", "thread": {"javaName": "worker", "javaThreadId": 1}},
            ],
            "jdk.CPULoad": [],
        }
        with patch("spw.cpu_thread_analysis.read_events_with_status", side_effect=_fake_read_events(event_map)):
            result = analyze_cpu_and_threads(Path("jfr.exe"), Path("test.jfr"))
        self.assertEqual(result["thread_lifecycle"]["started"], 2)
        self.assertEqual(result["thread_lifecycle"]["ended"], 1)
        self.assertEqual(result["thread_lifecycle"]["still_running_at_capture_end"], ["worker#2"])

    def test_passive_level_has_no_execution_samples(self) -> None:
        event_map = {"jdk.ExecutionSample": [], "jdk.ThreadCPULoad": [], "jdk.ThreadStart": [], "jdk.ThreadEnd": [], "jdk.CPULoad": [CPU_LOAD]}
        with patch("spw.cpu_thread_analysis.read_events_with_status", side_effect=_fake_read_events(event_map)):
            result = analyze_cpu_and_threads(Path("jfr.exe"), Path("test.jfr"))
        self.assertFalse(result["execution_samples_available"])
        self.assertEqual(result["total_execution_samples"], 0)


class AllocationGcAnalysisTests(unittest.TestCase):
    def test_analyze_allocation_and_gc(self) -> None:
        event_map = {
            "jdk.ObjectAllocationSample": [ALLOC_SAMPLE],
            "jdk.GCPhasePause": [GC_PAUSE],
            "jdk.GCPhasePauseLevel1": [],
            "jdk.GCPhasePauseLevel2": [],
            "jdk.GCPhasePauseLevel3": [],
            "jdk.GCPhasePauseLevel4": [],
            "jdk.GCHeapSummary": [HEAP_BEFORE, HEAP_AFTER],
        }
        with patch("spw.allocation_gc_analysis.read_events_with_status", side_effect=_fake_read_events(event_map)):
            result = analyze_allocation_and_gc(Path("jfr.exe"), Path("test.jfr"))
        self.assertTrue(result["allocation_samples_available"])
        self.assertEqual(result["allocation_by_class_bytes"]["[B"], 1024)
        self.assertEqual(result["gc_summary"]["pause_event_count"], 1)
        self.assertAlmostEqual(result["gc_summary"]["total_pause_seconds"], 0.002)
        self.assertEqual(result["heap_summary"]["high_water_mark_bytes"], 1000)
        self.assertEqual(result["heap_summary"]["reclaimed_bytes_by_gc_id"][0], 600)


class StartupAnalysisTests(unittest.TestCase):
    def test_time_to_first_sample_is_never_negative_relative_to_earliest_event(self) -> None:
        event_map = {"jdk.ClassLoadingStatistics": [CLASS_LOADING_1, CLASS_LOADING_2], "jdk.ExecutionSample": [EARLY_EXEC_SAMPLE]}
        with patch("spw.startup_analysis.read_events_with_status", side_effect=_fake_read_events(event_map)):
            result = analyze_startup(Path("jfr.exe"), Path("test.jfr"))
        # EARLY_EXEC_SAMPLE (00:00.5) is earlier than the first class-loading
        # sample (00:01), so it must become the zero point, not a negative offset.
        self.assertEqual(result["time_to_first_execution_sample_seconds"], 0.0)
        self.assertAlmostEqual(result["class_loading_curve"][0]["elapsed_seconds"], 0.5)


if __name__ == "__main__":
    unittest.main()
