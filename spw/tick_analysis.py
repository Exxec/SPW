from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from .jfr_events import parse_jfr_timestamp, read_events_with_status, top_frame

TICK_EVENT_TYPE = "com.spw.TickBoundary"
_STALL_TOP_FRAMES = 5


def analyze_ticks(jfr_tool: Path, recording_path: Path) -> dict[str, Any]:
    """Summarize campaign-tick boundaries from the optional SPW Tick Marker mod.

    This event only exists if the user installed and enabled the separate
    `spw/agent-mod` mod (an ordinary Starsector mod using the public
    `EveryFrameScript` API -- see the design doc's "Capture levels"
    section). Its absence is not an error: `ticks_available` is `False`
    and every other field is left empty, exactly like a `PASSIVE`
    recording correctly showing no execution samples -- unless
    `read_limitation` is set, in which case the absence reflects a failed
    read (e.g. a `jfr print` timeout on a very large recording), not a
    confirmed "mod not installed".
    """

    events, read_limitation = read_events_with_status(jfr_tool, recording_path, [TICK_EVENT_TYPE])
    if not events:
        return {
            "ticks_available": False,
            "tick_count": 0,
            "read_limitation": read_limitation,
            "limitations": (
                "No com.spw.TickBoundary events were found. This requires the optional SPW Tick Marker mod "
                "(spw/agent-mod) to be installed and enabled in the profiled installation, and the capture to "
                "be at the DEEP_DIAGNOSTIC level; its absence here does not indicate a problem."
                if not read_limitation
                else f"Could not read {TICK_EVENT_TYPE} events ({read_limitation}); absence here is inconclusive, not confirmation the mod is missing."
            ),
        }

    events.sort(key=lambda event: event.get("tickIndex", 0))
    elapsed_values = [event["elapsedSeconds"] for event in events if "elapsedSeconds" in event]
    total_elapsed = sum(elapsed_values) if elapsed_values else 0.0
    average_tick_seconds = (total_elapsed / len(elapsed_values)) if elapsed_values else None
    max_tick_seconds = max(elapsed_values) if elapsed_values else None

    # A tick much longer than the running average is evidence of a
    # real-time stall on the campaign-engine thread between two ticks --
    # not proof of a specific cause, just a located, timestamped window a
    # reader can then cross-reference against CPU/GC/allocation evidence
    # from the same recording.
    stall_threshold_seconds = (average_tick_seconds * 4) if average_tick_seconds else None
    stalls = []
    if stall_threshold_seconds:
        for event in events:
            seconds = event.get("elapsedSeconds")
            if seconds is not None and seconds > stall_threshold_seconds:
                stalls.append({"tick_index": event.get("tickIndex"), "elapsed_seconds": seconds, "start_time": event.get("startTime")})

    return {
        "ticks_available": True,
        "read_limitation": None,
        "tick_count": len(events),
        "average_tick_seconds": average_tick_seconds,
        "max_tick_seconds": max_tick_seconds,
        "stall_threshold_seconds": stall_threshold_seconds,
        "stalls": stalls,
        "limitations": "elapsedSeconds is the engine's own advance() argument, not a wall-clock measurement of tick duration; a paused campaign can also produce irregular values.",
    }


def correlate_stalls_with_execution_samples(tick_result: dict[str, Any], execution_samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """For each detected tick stall, report which sampled frames occurred during it.

    `elapsedSeconds` on a `com.spw.TickBoundary` event is the engine's own
    delta-time argument to `advance()`, measured up to the moment the tick
    fired (`startTime`) -- so a stall's window is `[startTime -
    elapsedSeconds, startTime]`, not after it. This is the actual payoff
    the tick-boundary event exists for: turning "tick 4,213 was 6x the
    average duration" into "...and here is what was sampled during it",
    rather than leaving a reader to cross-reference two JSON files by hand.

    Requires both `tick_result["stalls"]` (from `analyze_ticks`) and
    `STANDARD`/`DEEP_DIAGNOSTIC`-level execution samples; returns `[]` if
    either is missing, which correctly means "cannot correlate", not "no
    stalls happened".
    """

    stalls = tick_result.get("stalls") or []
    if not stalls or not execution_samples:
        return []

    parsed_samples = [(timestamp, event) for event in execution_samples if (timestamp := parse_jfr_timestamp(event.get("startTime"))) is not None]

    correlated = []
    for stall in stalls:
        stall_end = parse_jfr_timestamp(stall.get("start_time"))
        elapsed_seconds = stall.get("elapsed_seconds")
        if stall_end is None or elapsed_seconds is None:
            continue
        window_start_seconds = (stall_end.timestamp()) - elapsed_seconds

        frame_counts: Counter[tuple[str, str]] = Counter()
        samples_in_window = 0
        for timestamp, event in parsed_samples:
            if window_start_seconds <= timestamp.timestamp() <= stall_end.timestamp():
                samples_in_window += 1
                frame = top_frame(event)
                if frame and frame.get("class_name"):
                    frame_counts[(frame["class_name"], frame.get("method_name") or "?")] += 1

        correlated.append(
            {
                "tick_index": stall.get("tick_index"),
                "start_time": stall.get("start_time"),
                "elapsed_seconds": elapsed_seconds,
                "execution_samples_in_window": samples_in_window,
                "top_frames": [
                    {"class_name": class_name, "method_name": method_name, "samples": count}
                    for (class_name, method_name), count in frame_counts.most_common(_STALL_TOP_FRAMES)
                ],
            }
        )
    return correlated
