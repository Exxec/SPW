from __future__ import annotations

from pathlib import Path
from typing import Any

from .jfr_events import event_read_status, parse_jfr_timestamp, read_events_with_status


def analyze_startup(jfr_tool: Path, recording_path: Path) -> dict[str, Any]:
    """Give an approximate startup signal from a recording: a classloading curve and a rough first-activity proxy.

    Starsector does not expose a generic, engine-defined "reached main
    menu" marker to a recording captured this way. The `DEEP_DIAGNOSTIC`-only
    custom tick-boundary event (see `tick_analysis.py`) is a much better
    signal when the optional SPW Tick Marker mod is installed; this
    function reports the closest generic proxies available from stock JFR
    events for when it is not.
    """

    event_reads: dict[str, dict[str, Any]] = {}
    class_loading, limitation = read_events_with_status(jfr_tool, recording_path, ["jdk.ClassLoadingStatistics"])
    event_reads["jdk.ClassLoadingStatistics"] = event_read_status(class_loading, limitation)
    execution_samples, limitation = read_events_with_status(jfr_tool, recording_path, ["jdk.ExecutionSample"])
    event_reads["jdk.ExecutionSample"] = event_read_status(execution_samples, limitation)

    # A `DEEP_DIAGNOSTIC` capture can carry hundreds of thousands of
    # execution samples; only the minimum timestamp is ever needed from
    # that list, so it is computed with a single O(n) pass (`min`) rather
    # than sorting the whole list just to read its first element.
    class_loading_timestamps = [t for t in (parse_jfr_timestamp(event.get("startTime")) for event in class_loading) if t is not None]
    sample_timestamps = [t for t in (parse_jfr_timestamp(event.get("startTime")) for event in execution_samples) if t is not None]
    earliest_sample_timestamp = min(sample_timestamps) if sample_timestamps else None
    # The recording's own start time is the true zero point; the earliest
    # timestamp across every event type read here is the closest available
    # proxy for it (neither event stream necessarily begins at time zero on
    # its own -- jdk.ClassLoadingStatistics is periodic and may not fire
    # until well after the recording starts).
    candidate_reference_times = class_loading_timestamps + ([earliest_sample_timestamp] if earliest_sample_timestamp is not None else [])
    reference_time = min(candidate_reference_times) if candidate_reference_times else None

    class_loading_curve = []
    for event in sorted(class_loading, key=lambda e: e.get("startTime") or ""):
        timestamp = parse_jfr_timestamp(event.get("startTime"))
        if timestamp is None or reference_time is None:
            continue
        class_loading_curve.append(
            {
                "elapsed_seconds": (timestamp - reference_time).total_seconds(),
                "loaded_class_count": event.get("loadedClassCount"),
                "unloaded_class_count": event.get("unloadedClassCount"),
            }
        )

    time_to_first_execution_sample_seconds = None
    if earliest_sample_timestamp is not None and reference_time is not None:
        time_to_first_execution_sample_seconds = (earliest_sample_timestamp - reference_time).total_seconds()

    return {
        "class_loading_curve": class_loading_curve,
        "time_to_first_execution_sample_seconds": time_to_first_execution_sample_seconds,
        "limitations": (
            "No engine-defined startup-complete (e.g. main-menu-reached) marker is available from stock JFR "
            "events; time_to_first_execution_sample_seconds is a rough proxy only, and the class-loading curve "
            "reflects overall JVM class loading, not Starsector-specific milestones."
        ),
        "event_reads": event_reads,
    }
