from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .allocation_gc_analysis import analyze_allocation_and_gc
from .attribution import attribute_execution_samples
from .cpu_thread_analysis import analyze_cpu_and_threads
from .jfr_events import read_events_with_status
from .rendering_diagnostics import analyze_rendering
from .startup_analysis import analyze_startup
from .tick_analysis import analyze_ticks, correlate_stalls_with_execution_samples

SCHEMA_VERSION = "0.1.0"


def run_analysis(jfr_tool: Path, recording_path: Path, mod_ownership: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run the normalize/attribute/analyze stages of the pipeline against one recording.

    This is entirely external-process work per the design's collector/
    process split: it reads an already-written `.jfr` file and does all
    parsing, aggregation, and attribution here, never inside the profiled
    JVM.
    """

    execution_samples, execution_samples_limitation = read_events_with_status(jfr_tool, recording_path, ["jdk.ExecutionSample"])
    cpu_thread = analyze_cpu_and_threads(
        jfr_tool,
        recording_path,
        execution_samples=execution_samples,
        execution_samples_limitation=execution_samples_limitation,
    )
    allocation_gc = analyze_allocation_and_gc(jfr_tool, recording_path)
    startup = analyze_startup(jfr_tool, recording_path)
    rendering = analyze_rendering(cpu_thread)
    ticks = analyze_ticks(jfr_tool, recording_path)
    tick_stall_correlation = correlate_stalls_with_execution_samples(ticks, execution_samples)

    attribution = None
    if mod_ownership is not None:
        class_index = mod_ownership.get("class_index", {})
        package_prefix_index = mod_ownership.get("package_prefix_index", {})
        attribution = attribute_execution_samples(execution_samples, class_index, package_prefix_index)

    return {
        "schema_version": SCHEMA_VERSION,
        "recording": str(recording_path),
        "cpu_thread": cpu_thread,
        "allocation_gc": allocation_gc,
        "startup": startup,
        "rendering": rendering,
        "ticks": ticks,
        "tick_stall_correlation": tick_stall_correlation,
        "attribution": attribution,
    }


def render_analysis_markdown(analysis: dict[str, Any]) -> str:
    cpu_thread = analysis["cpu_thread"]
    allocation_gc = analysis["allocation_gc"]
    startup = analysis["startup"]
    attribution = analysis.get("attribution")

    lines = [
        "# SPW analysis report",
        "",
        f"- Recording: `{analysis['recording']}`",
        "",
        "## CPU and threads",
        "",
        f"- Execution samples available: {cpu_thread['execution_samples_available']} ({cpu_thread['total_execution_samples']} samples)",
        f"- Threads started/ended: {cpu_thread['thread_lifecycle']['started']}/{cpu_thread['thread_lifecycle']['ended']}",
    ]
    if cpu_thread["thread_lifecycle"]["still_running_at_capture_end"]:
        lines.append(f"- Still running at capture end: {', '.join(cpu_thread['thread_lifecycle']['still_running_at_capture_end'][:10])}")
    if cpu_thread["samples_by_top_frame"]:
        lines.append("- Top sampled frames:")
        for entry in cpu_thread["samples_by_top_frame"][:10]:
            lines.append(f"  - {entry['class_name']}.{entry['method_name']}: {entry['samples']} samples")
    lines.extend(["", "## Allocation and GC", ""])
    lines.append(f"- Allocation samples available: {allocation_gc['allocation_samples_available']}")
    if allocation_gc["allocation_by_class_bytes"]:
        lines.append("- Top allocating classes (sampled bytes):")
        for class_name, total in list(allocation_gc["allocation_by_class_bytes"].items())[:10]:
            lines.append(f"  - {class_name}: {total} bytes")
    gc_summary = allocation_gc["gc_summary"]
    lines.append(f"- GC pauses: {gc_summary['pause_event_count']}, total {gc_summary['total_pause_seconds']:.6f}s, max {gc_summary['max_pause_seconds']}")
    lines.append(f"- Heap high-water mark: {allocation_gc['heap_summary']['high_water_mark_bytes']} bytes")
    lines.extend(["", "## Startup (approximate)", ""])
    lines.append(f"- Time to first execution sample: {startup['time_to_first_execution_sample_seconds']} s")
    lines.append(f"- Limitation: {startup['limitations']}")
    ticks = analysis.get("ticks") or {}
    lines.extend(["", "## Campaign ticks (SPW Tick Marker mod, optional)", ""])
    if ticks.get("ticks_available"):
        lines.append(f"- Ticks observed: {ticks['tick_count']}")
        lines.append(f"- Average tick: {ticks['average_tick_seconds']} s, max: {ticks['max_tick_seconds']} s")
        if ticks.get("stalls"):
            lines.append(f"- Stalls (tick > 4x average): {len(ticks['stalls'])}")
            correlation_by_tick = {entry["tick_index"]: entry for entry in (analysis.get("tick_stall_correlation") or [])}
            for stall in ticks["stalls"][:10]:
                lines.append(f"  - tick {stall['tick_index']} at {stall['start_time']}: {stall['elapsed_seconds']} s")
                correlated = correlation_by_tick.get(stall["tick_index"])
                if correlated and correlated["top_frames"]:
                    lines.append(f"    sampled during this stall ({correlated['execution_samples_in_window']} samples):")
                    for frame in correlated["top_frames"]:
                        lines.append(f"      - {frame['class_name']}.{frame['method_name']}: {frame['samples']} samples")
                elif correlated:
                    lines.append("    no execution samples fell inside this stall's window")
    else:
        lines.append("Not available for this run (requires the optional SPW Tick Marker mod at DEEP_DIAGNOSTIC).")
    rendering = analysis.get("rendering") or {}
    lines.extend(["", "## Rendering (best-effort)", ""])
    if rendering.get("render_thread_names_matched"):
        lines.append(f"- Render-thread candidates: {', '.join(rendering['render_thread_names_matched'])}")
    else:
        lines.append("- No thread names matched common render-thread conventions.")
    lines.append(f"- Limitation: {rendering.get('limitations', '')}")
    lines.extend(["", "## Mod attribution", ""])
    if attribution is None:
        lines.append("No mod-ownership index was supplied; attribution was skipped.")
    else:
        lines.append(f"- Samples attributed: {attribution['total_samples_attributed']}")
        for owner, count in list(attribution["samples_by_owner"].items())[:10]:
            lines.append(f"  - {owner}: {count} samples")
    lines.extend(
        [
            "",
            "## Scope boundary",
            "",
            "Sample-based CPU/allocation figures are statistical, not exhaustive; startup timing is a rough proxy, not an engine-defined milestone; attribution reflects static class/package ownership, not confirmed causation.",
            "",
        ]
    )
    return "\n".join(lines)


def write_analysis_artifacts(analysis: dict[str, Any], output: Path) -> dict[str, Path]:
    output = output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    paths = {
        "cpu_thread": output / "cpu-thread-analysis.json",
        "allocation_gc": output / "allocation-gc-analysis.json",
        "startup": output / "startup-analysis.json",
        "rendering": output / "rendering-diagnostics.json",
        "ticks": output / "tick-analysis.json",
        "tick_stall_correlation": output / "tick-stall-correlation.json",
        "report": output / "ANALYSIS_REPORT.md",
    }
    paths["cpu_thread"].write_text(json.dumps(analysis["cpu_thread"], indent=2, sort_keys=True), encoding="utf-8")
    paths["allocation_gc"].write_text(json.dumps(analysis["allocation_gc"], indent=2, sort_keys=True), encoding="utf-8")
    paths["startup"].write_text(json.dumps(analysis["startup"], indent=2, sort_keys=True), encoding="utf-8")
    paths["rendering"].write_text(json.dumps(analysis["rendering"], indent=2, sort_keys=True), encoding="utf-8")
    paths["ticks"].write_text(json.dumps(analysis["ticks"], indent=2, sort_keys=True), encoding="utf-8")
    paths["tick_stall_correlation"].write_text(json.dumps(analysis["tick_stall_correlation"], indent=2, sort_keys=True), encoding="utf-8")
    if analysis.get("attribution") is not None:
        paths["attribution"] = output / "attribution.json"
        paths["attribution"].write_text(json.dumps(analysis["attribution"], indent=2, sort_keys=True), encoding="utf-8")
    paths["report"].write_text(render_analysis_markdown(analysis), encoding="utf-8")
    return paths
