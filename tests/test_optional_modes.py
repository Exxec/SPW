import json
import tempfile
import unittest
from pathlib import Path

from spw.benchmark import compute_frame_time_percentiles, load_scenario_manifest, manifest_hash
from spw.comparability import compute_comparability
from spw.rendering_diagnostics import analyze_rendering


def _snapshot(java_version="17.0.9", jvm_args=None, enabled_mods=None, fast_rendering="NOT_DETECTED", core_status="BASELINE_UNAVAILABLE", capture_level="STANDARD", gpus=None):
    return {
        "environment": {
            "java": {"java_version": java_version},
            "configured_jvm_arguments": jvm_args or [],
            "enabled_mod_order": enabled_mods or ["a", "b"],
            "gpus": {"gpus": gpus or [{"name": "NVIDIA GeForce RTX 5070", "driver_version": "32.0.16.1088"}]},
        },
        "core_integrity": {"status": core_status},
        "runtime_capabilities": [{"id": "FastRendering", "state": fast_rendering}],
        "capture_level": capture_level,
    }


class ComparabilityTests(unittest.TestCase):
    def test_identical_snapshots_are_comparable(self) -> None:
        snapshot = _snapshot()
        result = compute_comparability(snapshot, dict(snapshot))
        self.assertEqual(result["result"], "COMPARABLE")
        self.assertEqual(result["changed_variables"], [])

    def test_single_change_is_partially_controlled(self) -> None:
        a = _snapshot(java_version="17.0.9")
        b = _snapshot(java_version="21.0.1")
        result = compute_comparability(a, b)
        self.assertEqual(result["result"], "PARTIALLY_CONTROLLED")
        self.assertEqual(result["changed_variables"], ["java_major_version"])
        self.assertFalse(result["is_declared_experiment"])

    def test_declared_experiment_is_labeled(self) -> None:
        a = _snapshot(java_version="17.0.9")
        b = _snapshot(java_version="21.0.1")
        result = compute_comparability(a, b, declared_experiment_variable="java_major_version")
        self.assertTrue(result["is_declared_experiment"])

    def test_two_material_changes_are_not_comparable_with_matrix(self) -> None:
        a = _snapshot(java_version="17.0.9", fast_rendering="NOT_DETECTED")
        b = _snapshot(java_version="21.0.1", fast_rendering="ENABLED")
        result = compute_comparability(a, b)
        self.assertEqual(result["result"], "NOT_COMPARABLE")
        self.assertEqual(set(result["changed_variables"]), {"java_major_version", "fast_rendering_state"})
        self.assertEqual(len(result["proposed_controlled_run_matrix"]), 4)

    def test_mod_order_change_is_material(self) -> None:
        a = _snapshot(enabled_mods=["a", "b"])
        b = _snapshot(enabled_mods=["b", "a"])
        result = compute_comparability(a, b)
        self.assertIn("enabled_mod_set_or_order", result["changed_variables"])

    def test_gpu_driver_change_is_material(self) -> None:
        a = _snapshot(gpus=[{"name": "NVIDIA GeForce RTX 5070", "driver_version": "32.0.16.1088"}])
        b = _snapshot(gpus=[{"name": "NVIDIA GeForce RTX 5070", "driver_version": "32.0.17.0000"}])
        result = compute_comparability(a, b)
        self.assertIn("gpu_driver_state", result["changed_variables"])

    def test_gpu_list_order_does_not_matter(self) -> None:
        a = _snapshot(gpus=[{"name": "Intel", "driver_version": "1"}, {"name": "NVIDIA", "driver_version": "2"}])
        b = _snapshot(gpus=[{"name": "NVIDIA", "driver_version": "2"}, {"name": "Intel", "driver_version": "1"}])
        result = compute_comparability(a, b)
        self.assertEqual(result["result"], "COMPARABLE")


class BenchmarkTests(unittest.TestCase):
    def test_load_scenario_manifest_requires_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            path.write_text(json.dumps({"name": "combat-1"}), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_scenario_manifest(path)

    def test_manifest_hash_is_stable(self) -> None:
        manifest = {"name": "x", "java_executable": "java", "launch_args": ["-jar", "a.jar"], "duration_seconds": 30}
        self.assertEqual(manifest_hash(manifest), manifest_hash(dict(manifest)))

    def test_frame_time_percentiles_without_samples(self) -> None:
        result = compute_frame_time_percentiles([])
        self.assertFalse(result["samples_available"])

    def test_frame_time_percentiles_with_samples(self) -> None:
        samples = [0.016] * 95 + [0.05] * 5
        result = compute_frame_time_percentiles(samples)
        self.assertTrue(result["samples_available"])
        self.assertEqual(result["sample_count"], 100)
        self.assertGreaterEqual(result["p99_seconds"], result["p95_seconds"])
        self.assertGreaterEqual(result["spike_count"], 1)


def _exec_sample(thread_name: str, class_name: str) -> dict:
    return {
        "type": "jdk.ExecutionSample",
        "sampledThread": {"javaName": thread_name},
        "stackTrace": {"frames": [{"method": {"type": {"name": class_name.replace(".", "/")}, "name": "run"}, "lineNumber": 1, "type": "JIT compiled"}]},
    }


class RenderingDiagnosticsTests(unittest.TestCase):
    def test_matches_lwjgl_and_render_thread_names(self) -> None:
        cpu_thread = {
            "samples_by_thread": {"LWJGL Timer": 10, "pool-1-thread-1": 5},
            "average_thread_cpu_fraction": {"LWJGL Timer": 0.2, "pool-1-thread-1": 0.05},
        }
        result = analyze_rendering(cpu_thread)
        self.assertIn("LWJGL Timer", result["render_thread_names_matched"])
        self.assertNotIn("pool-1-thread-1", result["render_thread_names_matched"])
        self.assertEqual(result["execution_samples_on_render_threads"]["LWJGL Timer"], 10)

    def test_generically_named_thread_is_matched_by_render_related_stack_content(self) -> None:
        # The real gap found in a live capture: Starsector's own hottest
        # thread carried a JVM default name ("Thread-3") invisible to name
        # matching, but its sampled stacks were dominated by rendering
        # packages (here: Fast Rendering's own bridge package).
        cpu_thread = {
            "samples_by_thread": {"Thread-3": 4, "worker-1": 4},
            "average_thread_cpu_fraction": {"Thread-3": 0.5, "worker-1": 0.1},
        }
        execution_samples = (
            [_exec_sample("Thread-3", "com.genir.renderer.overrides.Sync")] * 3
            + [_exec_sample("Thread-3", "java.util.HashMap")]
            + [_exec_sample("worker-1", "com.example.somemod.Combat")] * 4
        )
        result = analyze_rendering(cpu_thread, execution_samples)
        self.assertIn("Thread-3", result["render_thread_content_matched"])
        self.assertNotIn("Thread-3", result["render_thread_names_matched"])
        self.assertIn("Thread-3", result["render_thread_names"])
        self.assertNotIn("worker-1", result["render_thread_names"])

    def test_without_execution_samples_falls_back_to_name_matching_only(self) -> None:
        cpu_thread = {"samples_by_thread": {"LWJGL Timer": 10}, "average_thread_cpu_fraction": {"LWJGL Timer": 0.2}}
        result = analyze_rendering(cpu_thread, None)
        self.assertEqual(result["render_thread_names"], ["LWJGL Timer"])
        self.assertEqual(result["render_thread_content_matched"], [])


if __name__ == "__main__":
    unittest.main()
