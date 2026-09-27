"""Versioned, read-only evidence exports for sister repositories."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

INVENTORY_SCHEMA = "spw-mod-inventory-1"
PERFORMANCE_SCHEMA = "spw-performance-1"


def _sha(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    except OSError:
        return None


def mod_identity_export(result: Any) -> dict[str, Any]:
    root = result.installation_path
    ids = Counter(mod["local_id"] for mod in result.mods)
    mods = []
    for mod in result.mods:
        relative = mod["relative_root"]
        mod_root = root / relative
        metadata_file = mod_root / ("mod_info.json.disabled" if mod["metadata_parse_status"] == "DISABLED" else "mod_info.json")
        mods.append({
            "id": mod["local_id"], "relative_root": relative,
            "enabled": mod["enabled"], "enabled_evidence": "metadata_renamed_disabled" if mod["metadata_parse_status"] == "DISABLED" else "enabled_mods_json" if result.enabled_mod_order is not None else "unknown",
            "metadata_status": mod["metadata_parse_status"], "metadata_sha256": _sha(metadata_file),
            "jars": [{"path": path, "sha256": _sha(root / path)} for path in mod["jar_paths"]],
            "duplicate_id": ids[mod["local_id"]] > 1,
        })
    enabled_hash = _sha(root / "mods" / "enabled_mods.json")
    fingerprint = hashlib.sha256(json.dumps({"enabled": enabled_hash, "mods": mods},
                                          sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return {
        "schema_version": INVENTORY_SCHEMA,
        "installation_path": str(root),
        "enabled_mods_sha256": enabled_hash,
        "install_fingerprint_sha256": fingerprint,
        "mods": mods,
        "findings": [f.__dict__ for f in result.findings if f.category == "mod-inventory"],
    }


def performance_export(analysis: dict[str, Any], inventory: dict[str, Any] | None,
                       artifact_hashes: dict[str, str | None], capture_level: str | None,
                       comparison: dict[str, Any] | None = None) -> dict[str, Any]:
    attribution = analysis.get("attribution") or {}
    event_status = (attribution.get("execution_samples_event_read") or {}).get("status")
    valid = event_status != "READ_FAILED" and isinstance(attribution.get("samples_by_owner"), dict)
    by_owner = attribution.get("samples_by_owner", {}) if valid else {}
    mods = []
    for mod in (inventory or {}).get("mods", []):
        mod_id = mod["id"]
        unambiguous = not mod["duplicate_id"]
        bound = bool(mod["metadata_sha256"])
        mods.append({
            "mod_id": mod_id, "relative_root": mod["relative_root"],
            "metadata_sha256": mod["metadata_sha256"], "enabled": mod["enabled"],
            "attribution_confidence": "MIXED_STATIC_OWNER" if valid and unambiguous and bound else "UNBOUND_OWNER_LABEL" if valid and unambiguous else "UNKNOWN",
            "attributed_samples": by_owner.get(mod_id) if valid and unambiguous else None,
            "limitation": "Aggregate does not preserve per-sample exactness" if valid and unambiguous and bound else "Identity hash unavailable" if valid and unambiguous else "ambiguous mod id or attribution unavailable",
        })
    return {
        "schema_version": PERFORMANCE_SCHEMA, "metric": "attributed_execution_samples",
        "unit": "samples", "capture_level": capture_level,
        "artifact_sha256": artifact_hashes, "mods": mods,
        "total_attributed_samples": attribution.get("total_samples_attributed") if valid else None,
        "startup_metric": None, "startup_limitation": "No engine-defined startup-complete or per-mod startup metric",
        "comparison": comparison,
        "limitations": [] if valid else ["Execution-sample attribution unavailable or failed"],
    }


def identity_from_ownership(ownership: dict[str, Any] | None) -> dict[str, Any] | None:
    """Recover identity labels for standalone analyze; hashes remain unavailable."""
    if ownership is None:
        return None
    source_mods = ownership.get("mods", [])
    ids = Counter(mod.get("local_id") for mod in source_mods if isinstance(mod, dict))
    return {"mods": [{"id": mod.get("local_id"), "relative_root": mod.get("relative_root"),
                      "metadata_sha256": None, "enabled": mod.get("enabled"),
                      "duplicate_id": ids[mod.get("local_id")] > 1}
                     for mod in source_mods if isinstance(mod, dict) and mod.get("local_id")]}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
