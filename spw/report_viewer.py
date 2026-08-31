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

# Plain string, not an f-string, so its own `{`/`}` don't need doubling --
# inserted as-is into the report's f-string below. Vanilla JS, no external
# library, consistent with the viewer's no-network/no-external-script
# design: click a `table.sortable` header to sort that column, ascending
# then descending on repeated clicks. `data-value` (see `_ranked_table`)
# is preferred when present so a formatted display string (e.g. "1,024")
# still sorts numerically; otherwise a cell's own text is parsed as a
# number when it looks like one, falling back to a case-insensitive
# string compare.
_SORT_SCRIPT = """
document.querySelectorAll('table.sortable').forEach(function (table) {
  var thead = table.tHead;
  var tbody = table.tBodies[0];
  if (!thead || !tbody) return;
  function cellValue(cell) {
    if (cell.hasAttribute('data-value')) {
      var v = cell.getAttribute('data-value');
      var n = parseFloat(v);
      return isNaN(n) ? v : n;
    }
    var text = cell.textContent.trim();
    var n = parseFloat(text);
    return (text !== '' && !isNaN(n) && /^-?[0-9.]+$/.test(text)) ? n : text.toLowerCase();
  }
  Array.prototype.forEach.call(thead.rows[0].cells, function (th, index) {
    th.addEventListener('click', function () {
      var ascending = th.getAttribute('data-sort-dir') !== 'asc';
      Array.prototype.forEach.call(thead.rows[0].cells, function (other) {
        other.removeAttribute('data-sort-dir');
        other.classList.remove('sorted-asc', 'sorted-desc');
      });
      th.setAttribute('data-sort-dir', ascending ? 'asc' : 'desc');
      th.classList.add(ascending ? 'sorted-asc' : 'sorted-desc');
      var rows = Array.prototype.slice.call(tbody.rows);
      rows.sort(function (a, b) {
        var av = cellValue(a.cells[index]);
        var bv = cellValue(b.cells[index]);
        if (av < bv) return ascending ? -1 : 1;
        if (av > bv) return ascending ? 1 : -1;
        return 0;
      });
      rows.forEach(function (row) { tbody.appendChild(row); });
    });
  });
});
"""


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
        "<table class='sortable'><thead><tr><th>Confidence</th><th>Severity</th><th>Category</th><th>Finding</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _ranked_table(headers: list[str], rows: list[tuple], empty_message: str) -> str:
    """A sortable table where the last column of each row is a number to sort on.

    `rows` entries are `(*label_cells, number)`; the number is rendered
    with a `data-value` attribute so the sort script orders it numerically
    even though the visible cell may carry other formatting.
    """

    if not rows:
        return f"<table class='sortable'><thead><tr>{''.join(f'<th>{_e(h)}</th>' for h in headers)}</tr></thead><tbody><tr><td colspan='{len(headers)}' class='muted'>{_e(empty_message)}</td></tr></tbody></table>"
    body_rows = []
    for row in rows:
        *label_cells, number = row
        cells = "".join(f"<td>{_e(cell)}</td>" for cell in label_cells)
        display = f"{number:,}" if isinstance(number, int) else str(number)
        cells += f"<td data-value='{_e(number)}'>{_e(display)}</td>"
        body_rows.append(f"<tr>{cells}</tr>")
    return f"<table class='sortable'><thead><tr>{''.join(f'<th>{_e(h)}</th>' for h in headers)}</tr></thead><tbody>{''.join(body_rows)}</tbody></table>"


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
        "<details open><summary>Environment</summary><table class='kv'>"
        f"<tr><th>Installation</th><td><code>{_e(environment.get('installation_path', 'UNKNOWN'))}</code></td></tr>"
        f"<tr><th>Profiler tooling JDK</th><td>{_e((environment.get('java') or {}).get('implementor', 'UNKNOWN'))} {_e((environment.get('java') or {}).get('java_version', 'UNKNOWN'))}</td></tr>"
        + (
            f"<tr><th>Target JVM</th><td>{_e((environment.get('target_jvm') or {}).get('vm_name', 'UNKNOWN'))} {_e((environment.get('target_jvm') or {}).get('jdk_version', 'UNKNOWN'))}</td></tr>"
            if (environment.get("target_jvm") or {}).get("jdk_version")
            else ""
        )
        + f"<tr><th>GPU(s)</th><td>{_e(gpu_list)}</td></tr>"
        f"<tr><th>Mods discovered</th><td>{_e(environment.get('mod_count', 'UNKNOWN'))}</td></tr>"
        f"<tr><th>Core-integrity status</th><td>{_e(core_integrity.get('status', 'BASELINE_UNAVAILABLE'))}</td></tr>"
        f"</table></details>",
        f"<details open><summary>Runtime capabilities</summary><ul>{capability_rows}</ul></details>",
    ]

    if mod_ownership.get("shared_package_prefixes") is not None:
        ambiguous_count = sum(1 for entry in mod_ownership.get("jar_ownership", []) if entry.get("confidence") == "AMBIGUOUS")
        sections.append(
            "<details open><summary>Mod ownership</summary><table class='kv'>"
            f"<tr><th>JARs indexed</th><td>{_e(len(mod_ownership.get('jar_ownership', [])))}</td></tr>"
            f"<tr><th>Shared package prefixes</th><td>{_e(len(mod_ownership.get('shared_package_prefixes', {})))}</td></tr>"
            f"<tr><th>Ambiguous JARs</th><td>{_e(ambiguous_count)}</td></tr>"
            "</table></details>"
        )

    def _read_failure_html(event_reads: dict) -> str:
        failed = [f"<li class='warn'><code>{_e(event_type)}</code>: {_e(info.get('reason'))}</li>" for event_type, info in (event_reads or {}).items() if info.get("status") == "READ_FAILED"]
        if not failed:
            return ""
        return "<p class='warn'>WARNING: the following event reads failed; affected counts above are unknown, not confirmed zero:</p><ul>" + "".join(failed) + "</ul>"

    if cpu_thread is not None:
        top_frames_table = _ranked_table(
            ["Class", "Method", "Samples"],
            [(f["class_name"], f["method_name"], f["samples"]) for f in cpu_thread.get("samples_by_top_frame", [])[:10]],
            "No samples.",
        )
        sections.append(
            "<details open><summary>CPU and threads</summary>"
            f"<p>Execution samples: {_e(cpu_thread.get('total_execution_samples', 0))} (available: {_e(cpu_thread.get('execution_samples_available'))})</p>"
            f"{_read_failure_html(cpu_thread.get('event_reads'))}"
            f"{top_frames_table}</details>"
        )

    if allocation_gc is not None:
        top_alloc_table = _ranked_table(
            ["Class", "Bytes"],
            [(name, total) for name, total in list(allocation_gc.get("allocation_by_class_bytes", {}).items())[:10]],
            "No allocation samples.",
        )
        sections.append(
            "<details open><summary>Allocation and GC</summary>"
            f"<p>GC pauses: {_e(allocation_gc.get('gc_summary', {}).get('pause_event_count', 0))}, "
            f"heap high-water mark: {_e(allocation_gc.get('heap_summary', {}).get('high_water_mark_bytes', 0))} bytes</p>"
            f"{_read_failure_html(allocation_gc.get('event_reads'))}"
            f"{top_alloc_table}</details>"
        )

    if ticks is not None:
        if ticks.get("ticks_available"):
            stalls_table = _ranked_table(
                ["Tick", "Start time", "Elapsed seconds"],
                [(s["tick_index"], s["start_time"], s["elapsed_seconds"]) for s in ticks.get("stalls", [])],
                "No stalls detected.",
            )
            sections.append(
                "<details open><summary>Campaign ticks</summary>"
                f"<p>Ticks observed: {_e(ticks['tick_count'])}, average: {_e(ticks.get('average_tick_seconds'))}s</p>"
                f"{stalls_table}</details>"
            )
        else:
            sections.append("<details open><summary>Campaign ticks</summary><p class='muted'>Not available (SPW Tick Marker mod not installed/enabled, or DEEP_DIAGNOSTIC not used).</p></details>")

    if comparison is not None:
        sections.append(
            "<details open><summary>Comparison</summary>"
            f"<p>Result: <span class='badge'>{_e(comparison.get('result'))}</span></p>"
            f"<p>Changed variables: {_e(', '.join(comparison.get('changed_variables', [])) or 'none')}</p></details>"
        )

    sections.append(f"<details open><summary>Findings ({len(all_findings)})</summary>{_findings_table(all_findings)}</details>")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>SPW report</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, sans-serif; max-width: 960px; margin: 2rem auto; padding: 0 1rem; color: #1a1a1a; background: #fafafa; }}
  h1 {{ font-size: 1.5rem; }}
  details {{ margin-bottom: 1.5rem; }}
  summary {{ font-size: 1.1rem; font-weight: 600; border-bottom: 1px solid #ddd; padding-bottom: 0.25rem; margin-top: 1rem; cursor: pointer; }}
  summary::marker {{ color: #888; }}
  details > *:not(summary) {{ margin-top: 0.75rem; }}
  table {{ border-collapse: collapse; width: 100%; }}
  table.kv th {{ text-align: left; width: 220px; color: #555; font-weight: normal; }}
  table:not(.kv) th {{ text-align: left; background: #eee; }}
  table:not(.kv) td, table:not(.kv) th {{ border: 1px solid #ddd; padding: 0.5rem; vertical-align: top; }}
  table.sortable th {{ cursor: pointer; user-select: none; }}
  table.sortable th:hover {{ background: #e0e0e0; }}
  table.sortable th.sorted-asc::after {{ content: " \\25B2"; font-size: 0.8em; }}
  table.sortable th.sorted-desc::after {{ content: " \\25BC"; font-size: 0.8em; }}
  code {{ background: #eee; padding: 0.1rem 0.3rem; border-radius: 3px; font-size: 0.9em; }}
  .muted {{ color: #888; }}
  .warn {{ color: #a15c00; }}
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
<p class="muted">Generated from <code>{_e(artifacts_dir)}</code>. This describes evidence and confidence only; see the accompanying Markdown reports for full scope-boundary language. Section headers collapse/expand on click; table columns sort on click.</p>
{''.join(sections)}
<script>{_SORT_SCRIPT}</script>
</body>
</html>
"""
