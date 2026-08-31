from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from .models import FingerprintResult


def render_markdown(result: FingerprintResult, capture: dict[str, object] | None = None) -> str:
    counts = Counter(finding.confidence for finding in result.findings)
    gpu_descriptions = [f"{gpu.get('name', 'UNKNOWN')} (driver {gpu.get('driver_version', 'UNKNOWN')})" for gpu in result.gpus.get("gpus", [])]
    lines = [
        "# Starsector Performance Workbench report",
        "",
        "## Environment summary",
        "",
        f"- Installation: `{result.installation_path}`",
        f"- Java: {result.java.get('implementor', 'UNKNOWN')} {result.java.get('java_version', 'UNKNOWN')} (source: {result.java.get('source', 'UNAVAILABLE')})",
        f"- GPU(s): {', '.join(gpu_descriptions) if gpu_descriptions else 'UNAVAILABLE'}",
        f"- Mods discovered: {len(result.mods)}",
        f"- Enabled-mod order known: {'yes' if result.enabled_mod_order is not None else 'no'}",
        f"- Core-integrity status: {result.core_integrity.get('status', 'BASELINE_UNAVAILABLE')}",
        f"- Shared package prefixes (bundled by more than one mod): {len(result.shared_package_prefixes)}",
        f"- Findings: {len(result.findings)}",
    ]
    if counts:
        lines.append("- Finding confidence breakdown: " + ", ".join(f"{level} {count}" for level, count in sorted(counts.items())))
    lines.extend(["", "## Runtime capabilities", ""])
    if not result.runtime_capabilities:
        lines.append("Not detected for this run.")
    else:
        for capability in result.runtime_capabilities:
            if capability["state"] == "NOT_DETECTED":
                continue
            version_suffix = f" {capability['version']}" if capability.get("version") else ""
            lines.append(f"- **{capability['id']}{version_suffix}**: {capability['state']}")
    lines.extend(["", "## Capture", ""])
    if capture is None:
        lines.append("No capture was requested for this run.")
    else:
        lines.append(f"- Type: {capture.get('capture_type')}")
        lines.append(f"- Target: {capture.get('target')}")
        if capture.get("output_path"):
            lines.append(f"- Recording: `{capture['output_path']}`")
        if capture.get("incomplete_reason"):
            lines.append(f"- Limitation: {capture['incomplete_reason']}")
    lines.extend(["", "## Findings", ""])
    if not result.findings:
        lines.append("No findings. This only means the V0.1 checks found no issues; it is not proof the installation is fully understood.")
    for finding in result.findings:
        location = f" — `{finding.file}`" if finding.file else ""
        lines.append(f"### [{finding.confidence}] {finding.id}{location}")
        lines.append("")
        lines.append(f"- Category: {finding.category}")
        lines.append(f"- Severity: {finding.severity}")
        lines.append(f"- {finding.explanation}")
        if finding.evidence:
            lines.append(f"- Evidence: {', '.join(finding.evidence)}")
        lines.append("")
    lines.extend(
        [
            "## Scope boundary",
            "",
            "This report describes the environment and inventory only. It does not attribute any performance cost to a mod; attribution and analysis arrive in later SPW versions.",
            "",
        ]
    )
    return "\n".join(lines)


def write_artifacts(result: FingerprintResult, output: Path, capture: dict[str, object] | None = None) -> dict[str, Path]:
    output = output.expanduser().resolve()
    try:
        output.relative_to(result.installation_path)
    except ValueError:
        pass
    else:
        raise ValueError("Output directory must not be inside the installation directory; this preserves the installation unchanged.")

    output.mkdir(parents=True, exist_ok=True)
    paths = {
        "environment": output / "environment.json",
        "core_integrity": output / "core-integrity.json",
        "mod_ownership": output / "mod-ownership.json",
        "runtime_capabilities": output / "runtime-capabilities.json",
        "report": output / "PERFORMANCE_REPORT.md",
    }
    paths["environment"].write_text(json.dumps(result.environment_dict(), indent=2, sort_keys=True), encoding="utf-8")
    paths["core_integrity"].write_text(json.dumps(result.core_integrity_dict(), indent=2, sort_keys=True), encoding="utf-8")
    paths["mod_ownership"].write_text(json.dumps(result.mod_ownership_dict(), indent=2, sort_keys=True), encoding="utf-8")
    paths["runtime_capabilities"].write_text(json.dumps(result.runtime_capabilities_dict(), indent=2, sort_keys=True), encoding="utf-8")
    paths["report"].write_text(render_markdown(result, capture), encoding="utf-8")
    return paths
