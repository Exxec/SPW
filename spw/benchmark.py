from __future__ import annotations

import hashlib
import json
import statistics
from pathlib import Path
from typing import Any

from .capture import DEFAULT_LEVEL, run_launch_capture
from .models import FingerprintResult

REQUIRED_MANIFEST_FIELDS = ("name", "java_executable", "launch_args", "duration_seconds")


def load_scenario_manifest(path: Path) -> dict[str, Any]:
    """Load and validate an explicit, user-authored benchmark scenario manifest.

    This is optional-mode plumbing (design doc roadmap V0.7): SPW never
    invents or infers a scenario; every field here is something the user
    wrote down.
    """

    manifest = json.loads(path.read_text(encoding="utf-8"))
    missing = [field for field in REQUIRED_MANIFEST_FIELDS if field not in manifest]
    if missing:
        raise ValueError(f"Scenario manifest is missing required field(s): {', '.join(missing)}")
    return manifest


def manifest_hash(manifest: dict[str, Any]) -> str:
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_frame_time_percentiles(samples_seconds: list[float]) -> dict[str, Any]:
    """Compute frame-time percentiles from an explicit list of per-frame durations.

    Starsector does not emit a standard JFR frame-render event, so these
    samples must come from an external source (e.g. a future in-game
    marker, or a user-supplied capture) -- this function never fabricates
    them. Returns a `samples_available: False` result when given none.
    """

    if not samples_seconds:
        return {"samples_available": False, "sample_count": 0}
    sorted_samples = sorted(samples_seconds)
    median = statistics.median(sorted_samples)
    spike_threshold = median * 2
    return {
        "samples_available": True,
        "sample_count": len(sorted_samples),
        "mean_seconds": statistics.mean(sorted_samples),
        "median_seconds": median,
        "p95_seconds": sorted_samples[min(len(sorted_samples) - 1, int(len(sorted_samples) * 0.95))],
        "p99_seconds": sorted_samples[min(len(sorted_samples) - 1, int(len(sorted_samples) * 0.99))],
        "max_seconds": sorted_samples[-1],
        "spike_threshold_seconds": spike_threshold,
        "spike_count": sum(1 for sample in sorted_samples if sample > spike_threshold),
    }


def run_benchmark(result: FingerprintResult, manifest: dict[str, Any], output_dir: Path, frame_time_samples_seconds: list[float] | None = None) -> dict[str, Any]:
    """Run one benchmark scenario: a launch-mode capture plus optional externally supplied frame-time samples."""

    java_executable = Path(manifest["java_executable"])
    level = manifest.get("level", DEFAULT_LEVEL)
    output_file = output_dir / "profile.jfr"
    capture_descriptor = run_launch_capture(
        result,
        java_executable=java_executable,
        launch_args=list(manifest["launch_args"]),
        output_file=output_file,
        duration_seconds=int(manifest["duration_seconds"]),
        level=level,
    )

    frame_time_percentiles = compute_frame_time_percentiles(frame_time_samples_seconds or [])
    if not frame_time_percentiles["samples_available"]:
        result.add(
            id="no-frame-time-samples",
            category="benchmark",
            severity="low",
            confidence="DETERMINISTIC",
            explanation="No frame-time samples were supplied for this benchmark run; frame-time percentiles are unavailable rather than estimated.",
        )

    return {
        "scenario_manifest_hash": manifest_hash(manifest),
        "scenario_name": manifest["name"],
        "run_settings": {"level": level, "duration_seconds": manifest["duration_seconds"], "launch_args": manifest["launch_args"]},
        "capture": capture_descriptor,
        "frame_time_percentiles": frame_time_percentiles,
    }
