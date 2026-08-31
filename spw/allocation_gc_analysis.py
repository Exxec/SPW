from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .jfr_events import parse_iso_duration_seconds, read_events_with_status, top_frame

_TOP_N = 25
_GC_PAUSE_EVENT_TYPES = ["jdk.GCPhasePause", "jdk.GCPhasePauseLevel1", "jdk.GCPhasePauseLevel2", "jdk.GCPhasePauseLevel3", "jdk.GCPhasePauseLevel4"]


def analyze_allocation_and_gc(jfr_tool: Path, recording_path: Path) -> dict[str, Any]:
    """Summarize allocation pressure, GC pause behavior, and heap growth from a recording.

    Allocation-sample events require `STANDARD` or `DEEP_DIAGNOSTIC`;
    `PASSIVE` recordings only carry GC pause/heap events, so a `PASSIVE`
    report correctly shows no allocation data rather than an error. Check
    `read_limitations` before treating a zero count as genuine, though --
    it means the underlying `jfr print` read failed, not that nothing
    happened.
    """

    read_limitations: dict[str, str] = {}
    allocation_samples, limitation = read_events_with_status(jfr_tool, recording_path, ["jdk.ObjectAllocationSample"])
    if limitation:
        read_limitations["jdk.ObjectAllocationSample"] = limitation
    gc_pauses, limitation = read_events_with_status(jfr_tool, recording_path, _GC_PAUSE_EVENT_TYPES)
    if limitation:
        read_limitations["gc_pause_events"] = limitation
    heap_summaries, limitation = read_events_with_status(jfr_tool, recording_path, ["jdk.GCHeapSummary"])
    if limitation:
        read_limitations["jdk.GCHeapSummary"] = limitation

    allocation_by_class: Counter[str] = Counter()
    allocation_by_frame: Counter[tuple[str, str]] = Counter()
    for event in allocation_samples:
        object_class = event.get("objectClass") or {}
        class_name = (object_class.get("name") or "UNKNOWN").replace("/", ".")
        weight = event.get("weight") or 0
        allocation_by_class[class_name] += weight
        frame = top_frame(event)
        if frame and frame.get("class_name"):
            allocation_by_frame[(frame["class_name"], frame.get("method_name") or "?")] += weight

    pause_durations = [parse_iso_duration_seconds(event.get("duration")) for event in gc_pauses]
    pause_durations = [duration for duration in pause_durations if duration is not None]
    gc_summary = {
        "pause_event_count": len(pause_durations),
        "total_pause_seconds": sum(pause_durations) if pause_durations else 0.0,
        "average_pause_seconds": (sum(pause_durations) / len(pause_durations)) if pause_durations else None,
        "max_pause_seconds": max(pause_durations) if pause_durations else None,
    }

    heap_used_before: dict[int, int] = {}
    heap_used_after: dict[int, int] = {}
    high_water_mark = 0
    for event in heap_summaries:
        gc_id = event.get("gcId")
        heap_used = event.get("heapUsed") or 0
        high_water_mark = max(high_water_mark, heap_used)
        if event.get("when") == "Before GC" and gc_id is not None:
            heap_used_before[gc_id] = heap_used
        elif event.get("when") == "After GC" and gc_id is not None:
            heap_used_after[gc_id] = heap_used

    reclaimed_by_gc = {
        gc_id: heap_used_before[gc_id] - heap_used_after[gc_id]
        for gc_id in heap_used_before
        if gc_id in heap_used_after
    }

    return {
        "allocation_samples_available": bool(allocation_samples),
        "allocation_by_class_bytes": dict(allocation_by_class.most_common(_TOP_N)),
        "allocation_by_top_frame_bytes": [
            {"class_name": class_name, "method_name": method_name, "bytes": total}
            for (class_name, method_name), total in allocation_by_frame.most_common(_TOP_N)
        ],
        "gc_summary": gc_summary,
        "heap_summary": {
            "high_water_mark_bytes": high_water_mark,
            "reclaimed_bytes_by_gc_id": reclaimed_by_gc,
        },
        "read_limitations": read_limitations,
    }
