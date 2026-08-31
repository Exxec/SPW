from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

# Each artifact may live directly in an `spw inventory`/`spw capture`
# output directory, or nested under `inventory/`/`analysis/` in an
# `spw diagnose` output directory -- checked in this order so both
# layouts work without the caller needing to know which one it is.
_SEARCH_SUBDIRS = ("", "inventory", "analysis", "compare")


def _load_first(artifacts_dir: Path, filename: str) -> dict[str, Any] | list[Any] | None:
    for subdir in _SEARCH_SUBDIRS:
        path = artifacts_dir / subdir / filename if subdir else artifacts_dir / filename
        if path.is_file():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
    return None


def _e(value: Any) -> str:
    """Escape a value for safe inclusion in the generated HTML (findings/mod names originate in untrusted mod files)."""

    return html.escape(str(value), quote=True)


def _findings_table(findings: list[dict[str, Any]]) -> str:
    if not findings:
        return "<p class='muted'>No findings.</p>"
    rows = []
    for finding in findings:
        evidence = ", ".join(finding.get("evidence") or [])
        location = f"<br><code>{_e(finding['file'])}</code>" if finding.get("file") else ""
        rows.append(
            "<tr>"
            f"<td><span class='badge badge-{_e(finding.get('confidence', '')).lower()}'>{_e(finding.get('confidence', ''))}</span></td>"
            f"<td>{_e(finding.get('severity', ''))}</td>"
            f"<td>{_e(finding.get('category', ''))}</td>"
            f"<td><strong>{_e(finding.get('id', ''))}</strong>{location}<div class='explanation'>{_e(finding.get('explanation', ''))}</div>"
            + (f"<div class='evidence'>Evidence: {_e(evidence)}</div>" if evidence else "")
            + "</td>"
            "</tr>"
        )
    return (
        "<table><thead><tr><th>Confidence</th><th>Severity</th><th>Category</th><th>Finding</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def render_html_report(artifacts_dir: Path) -> str:
    """Render a single, self-contained, offline HTML page summarizing an SPW artifacts directory.

    Reads whichever of environment.json / core-integrity.json /
    mod-ownership.json / runtime-capabilities.json / the analysis-stage
    JSON files / comparison.json are present; every section is optional,
    so this renders something useful against an inventory-only,
    capture+analyze-only, or full `spw diagnose` output alike. No network
    requests and no external scripts/styles, so it opens directly from
    disk (file://) with nothing else running.
    """

    environment = _load_first(artifacts_dir, "environment.json") or {}
    core_integrity = _load_first(artifacts_dir, "core-integrity.json") or {}
    mod_ownership = _load_first(artifacts_dir, "mod-ownership.json") or {}
    runtime_capabilities = (_load_first(artifacts_dir, "runtime-capabilities.json") or {}).get("capabilities", [])
    cpu_thread = _load_first(artifacts_dir, "cpu-thread-analysis.json")
    allocation_gc = _load_first(artifacts_dir, "allocation-gc-analysis.json")
    ticks = _load_first(artifacts_dir, "tick-analysis.json")
    comparison = _load_first(artifacts_dir, "comparison.json")

    all_findings = list(environment.get("findings") or [])
    seen_ids = {(f.get("id"), f.get("file")) for f in all_findings}
    for extra in (core_integrity.get("findings") or []) + (mod_ownership.get("findings") or []):
        key = (extra.get("id"), extra.get("file"))
        if key not in seen_ids:
            all_findings.append(extra)
            seen_ids.add(key)

    gpu_list = ", ".join(f"{g.get('name', 'UNKNOWN')} (driver {g.get('driver_version', 'UNKNOWN')})" for g in (environment.get("gpus") or {}).get("gpus", [])) or "UNAVAILABLE"

    capability_items = []
    for capability in runtime_capabilities:
        if capability.get("state") == "NOT_DETECTED":
            continue
        version_suffix = f" {_e(capability['version'])}" if capability.get("version") else ""
        capability_items.append(f"<li><strong>{_e(capability['id'])}</strong>{version_suffix}: {_e(capability['state'])}</li>")
    capability_rows = "".join(capability_items) or "<li class='muted'>None detected.</li>"

    sections = [
        "<section><h2>Environment</h2><table class='kv'>"
        f"<tr><th>Installation</th><td><code>{_e(environment.get('installation_path', 'UNKNOWN'))}</code></td></tr>"
        f"<tr><th>Java</th><td>{_e((environment.get('java') or {}).get('implementor', 'UNKNOWN'))} {_e((environment.get('java') or {}).get('java_version', 'UNKNOWN'))}</td></tr>"
        f"<tr><th>GPU(s)</th><td>{_e(gpu_list)}</td></tr>"
        f"<tr><th>Mods discovered</th><td>{_e(environment.get('mod_count', 'UNKNOWN'))}</td></tr>"
        f"<tr><th>Core-integrity status</th><td>{_e(core_integrity.get('status', 'BASELINE_UNAVAILABLE'))}</td></tr>"
        f"</table></section>",
        f"<section><h2>Runtime capabilities</h2><ul>{capability_rows}</ul></section>",
    ]

    if mod_ownership.get("shared_package_prefixes") is not None:
        ambiguous_count = sum(1 for entry in mod_ownership.get("jar_ownership", []) if entry.get("confidence") == "AMBIGUOUS")
        sections.append(
            "<section><h2>Mod ownership</h2><table class='kv'>"
            f"<tr><th>JARs indexed</th><td>{_e(len(mod_ownership.get('jar_ownership', [])))}</td></tr>"
            f"<tr><th>Shared package prefixes</th><td>{_e(len(mod_ownership.get('shared_package_prefixes', {})))}</td></tr>"
            f"<tr><th>Ambiguous JARs</th><td>{_e(ambiguous_count)}</td></tr>"
            "</table></section>"
        )

    _no_samples = "<li class='muted'>No samples.</li>"
    _no_allocation_samples = "<li class='muted'>No allocation samples.</li>"
    _no_stalls = "<li class='muted'>No stalls detected.</li>"

    if cpu_thread is not None:
        top_frames = "".join(f"<li><code>{_e(f['class_name'])}.{_e(f['method_name'])}</code>: {_e(f['samples'])} samples</li>" for f in cpu_thread.get("samples_by_top_frame", [])[:10])
        top_frames = top_frames or _no_samples
        sections.append(
            "<section><h2>CPU and threads</h2>"
            f"<p>Execution samples: {_e(cpu_thread.get('total_execution_samples', 0))} (available: {_e(cpu_thread.get('execution_samples_available'))})</p>"
            f"<ul>{top_frames}</ul></section>"
        )

    if allocation_gc is not None:
        top_alloc = "".join(f"<li><code>{_e(name)}</code>: {_e(total)} bytes</li>" for name, total in list(allocation_gc.get("allocation_by_class_bytes", {}).items())[:10])
        top_alloc = top_alloc or _no_allocation_samples
        sections.append(
            "<section><h2>Allocation and GC</h2>"
            f"<p>GC pauses: {_e(allocation_gc.get('gc_summary', {}).get('pause_event_count', 0))}, "
            f"heap high-water mark: {_e(allocation_gc.get('heap_summary', {}).get('high_water_mark_bytes', 0))} bytes</p>"
            f"<ul>{top_alloc}</ul></section>"
        )

    if ticks is not None:
        if ticks.get("ticks_available"):
            stall_items = "".join(f"<li>tick {_e(s['tick_index'])} at {_e(s['start_time'])}: {_e(s['elapsed_seconds'])}s</li>" for s in ticks.get("stalls", []))
            stall_items = stall_items or _no_stalls
            sections.append(
                "<section><h2>Campaign ticks</h2>"
                f"<p>Ticks observed: {_e(ticks['tick_count'])}, average: {_e(ticks.get('average_tick_seconds'))}s</p>"
                f"<ul>{stall_items}</ul></section>"
            )
        else:
            sections.append("<section><h2>Campaign ticks</h2><p class='muted'>Not available (SPW Tick Marker mod not installed/enabled, or DEEP_DIAGNOSTIC not used).</p></section>")

    if comparison is not None:
        sections.append(
            "<section><h2>Comparison</h2>"
            f"<p>Result: <span class='badge'>{_e(comparison.get('result'))}</span></p>"
            f"<p>Changed variables: {_e(', '.join(comparison.get('changed_variables', [])) or 'none')}</p></section>"
        )

    sections.append(f"<section><h2>Findings ({len(all_findings)})</h2>{_findings_table(all_findings)}</section>")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>SPW report</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, sans-serif; max-width: 960px; margin: 2rem auto; padding: 0 1rem; color: #1a1a1a; background: #fafafa; }}
  h1 {{ font-size: 1.5rem; }}
  h2 {{ font-size: 1.1rem; border-bottom: 1px solid #ddd; padding-bottom: 0.25rem; margin-top: 2rem; }}
  section {{ margin-bottom: 1.5rem; }}
  table {{ border-collapse: collapse; width: 100%; }}
  table.kv th {{ text-align: left; width: 220px; color: #555; font-weight: normal; }}
  table:not(.kv) th {{ text-align: left; background: #eee; }}
  table:not(.kv) td, table:not(.kv) th {{ border: 1px solid #ddd; padding: 0.5rem; vertical-align: top; }}
  code {{ background: #eee; padding: 0.1rem 0.3rem; border-radius: 3px; font-size: 0.9em; }}
  .muted {{ color: #888; }}
  .explanation {{ margin-top: 0.25rem; }}
  .evidence {{ margin-top: 0.25rem; font-size: 0.85em; color: #555; }}
  .badge {{ display: inline-block; padding: 0.1rem 0.5rem; border-radius: 3px; background: #ddd; font-size: 0.85em; }}
  .badge-deterministic {{ background: #cce5ff; }}
  .badge-high {{ background: #d4edda; }}
  .badge-low {{ background: #fff3cd; }}
</style>
</head>
<body>
<h1>Starsector Performance Workbench report</h1>
<p class="muted">Generated from <code>{_e(artifacts_dir)}</code>. This describes evidence and confidence only; see the accompanying Markdown reports for full scope-boundary language.</p>
{''.join(sections)}
</body>
</html>
"""
