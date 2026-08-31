from __future__ import annotations

from pathlib import Path

from .capture import run_attach_capture
from .capture_levels import LEVELS
from .jfr_events import event_read_status, jfr_tool_path, read_events_with_status
from .models import FingerprintResult


def _average_jvm_cpu(jfr_tool: Path, recording_path: Path) -> tuple[float | None, dict[str, object]]:
    """Return the average `jdk.CPULoad` fraction plus its `event_read_status` record.

    A `None` average is ambiguous on its own -- it means either "no
    jdk.CPULoad events were present" or "the jfr print read failed" -- so
    the caller must consult the returned status record to tell those apart
    rather than treating a `None` overhead figure as a confirmed zero-cost
    measurement.
    """

    events, limitation = read_events_with_status(jfr_tool, recording_path, ["jdk.CPULoad"])
    status = event_read_status(events, limitation)
    samples = [event["jvmUser"] + event["jvmSystem"] for event in events if "jvmUser" in event and "jvmSystem" in event]
    if not samples:
        return None, status
    return sum(samples) / len(samples), status


def estimate_attach_overhead(
    result: FingerprintResult,
    pid: int,
    level: str,
    output_dir: Path,
    window_seconds: int = 10,
    jcmd_path: Path | None = None,
    java_executable: Path | None = None,
) -> dict[str, object]:
    """Estimate the requested level's collector overhead via a short, sequential paired baseline.

    This measures the whole JVM's reported CPU load (`jdk.CPULoad`, which
    already includes JFR's own cost) over a short `PASSIVE` window and a
    short window at the requested level, against the same running process.
    The two windows are sequential, not simultaneous, so whatever the game
    itself was doing differently between them is a real source of noise in
    the result -- that limitation is recorded alongside the estimate rather
    than hidden by it. Collector-thread-specific CPU time is not separately
    exposed by standard JFR events across JDK versions, so this delta is
    used as the overhead figure instead of a per-thread breakdown.
    """

    estimate: dict[str, object] = {
        "capture_level": level,
        "measurement_method": "paired-baseline (sequential short windows)",
        "window_seconds": window_seconds,
        "baseline_jvm_cpu_fraction": None,
        "level_jvm_cpu_fraction": None,
        "estimated_overhead_fraction": None,
        "confidence": "UNAVAILABLE",
        "limitations": "Sequential windows, not simultaneous; the target process's own activity may differ between the two windows.",
        "event_reads": {},
    }
    if level not in LEVELS:
        raise ValueError(f"Unknown capture level: {level!r}; expected one of {LEVELS}")

    resolved_jfr_tool = jfr_tool_path(java_executable, None)
    if resolved_jfr_tool is None:
        result.add(
            id="jfr-tool-unavailable",
            category="overhead",
            severity="medium",
            confidence="DETERMINISTIC",
            explanation="No jfr executable could be located to read back CPU-load events for overhead estimation.",
        )
        return estimate

    baseline_file = output_dir / "overhead-baseline.jfr"
    baseline_descriptor = run_attach_capture(result, pid=pid, output_file=baseline_file, duration_seconds=window_seconds, level="PASSIVE", jcmd_path=jcmd_path, java_executable=java_executable)
    if baseline_descriptor.get("incomplete_reason"):
        estimate["limitations"] = f"Baseline window failed: {baseline_descriptor['incomplete_reason']}"
        return estimate
    baseline_cpu, baseline_status = _average_jvm_cpu(resolved_jfr_tool, baseline_file)
    estimate["event_reads"]["baseline"] = baseline_status

    if level == "PASSIVE":
        level_cpu, level_status = baseline_cpu, baseline_status
        estimate["limitations"] = "Requested level is PASSIVE, the same as the baseline; only one window was captured, and the zero-overhead result follows by definition rather than from an independent measurement."
    else:
        level_file = output_dir / "overhead-level.jfr"
        level_descriptor = run_attach_capture(result, pid=pid, output_file=level_file, duration_seconds=window_seconds, level=level, jcmd_path=jcmd_path, java_executable=java_executable)
        if level_descriptor.get("incomplete_reason"):
            estimate["limitations"] = f"Level window failed: {level_descriptor['incomplete_reason']}"
            return estimate
        level_cpu, level_status = _average_jvm_cpu(resolved_jfr_tool, level_file)
    estimate["event_reads"]["level"] = level_status

    estimate["baseline_jvm_cpu_fraction"] = baseline_cpu
    estimate["level_jvm_cpu_fraction"] = level_cpu
    if baseline_cpu is not None and level_cpu is not None:
        estimate["estimated_overhead_fraction"] = max(0.0, level_cpu - baseline_cpu)
        estimate["confidence"] = "LOW" if level != "PASSIVE" else "DETERMINISTIC"
    elif baseline_status["status"] == "READ_FAILED" or level_status["status"] == "READ_FAILED":
        result.add(
            id="overhead-cpu-load-read-failed",
            category="overhead",
            severity="low",
            confidence="DETERMINISTIC",
            explanation=f"Reading jdk.CPULoad events failed for at least one overhead-estimation window (baseline: {baseline_status['reason']}, level: {level_status['reason']}); the missing figure is unknown, not a confirmed zero.",
        )
    else:
        result.add(
            id="overhead-cpu-load-unavailable",
            category="overhead",
            severity="low",
            confidence="DETERMINISTIC",
            explanation="jdk.CPULoad events were not present in one or both overhead-estimation windows.",
        )
    return estimate
