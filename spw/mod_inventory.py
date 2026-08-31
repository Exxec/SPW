from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import lenient_json
from .models import FingerprintResult

# Must match the "id" declared in spw/agent-mod/mod_info.json.
TICK_MARKER_MOD_ID = "spw_tick_marker"


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _read_enabled_mods(mods_dir: Path, result: FingerprintResult) -> list[str] | None:
    path = mods_dir / "enabled_mods.json"
    if not path.is_file():
        result.add(
            id="missing-enabled-mods",
            category="mod-inventory",
            severity="medium",
            confidence="DETERMINISTIC",
            explanation="mods/enabled_mods.json was not found; enabled-mod order is unknown and every discovered mod directory is inventoried as UNVERIFIED-enabled status.",
        )
        return None
    try:
        data = lenient_json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        result.add(
            id="invalid-enabled-mods",
            category="mod-inventory",
            severity="high",
            confidence="DETERMINISTIC",
            explanation=f"mods/enabled_mods.json could not be parsed: {exc}",
            file="mods/enabled_mods.json",
        )
        return None
    enabled = data.get("enabledMods")
    if not isinstance(enabled, list):
        result.add(
            id="malformed-enabled-mods",
            category="mod-inventory",
            severity="high",
            confidence="DETERMINISTIC",
            explanation="mods/enabled_mods.json has no `enabledMods` list.",
            file="mods/enabled_mods.json",
        )
        return None
    return [str(item) for item in enabled]


def _parse_mod_info_file(info_path: Path, root: Path, result: FingerprintResult, mod_dir: Path, parse_status_on_success: str) -> dict[str, Any] | None:
    try:
        raw = lenient_json.loads(info_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        result.add(
            id="invalid-mod-info",
            category="mod-inventory",
            severity="high",
            confidence="DETERMINISTIC",
            explanation=f"{info_path.name} could not be parsed: {exc}",
            file=_relative(root, info_path),
        )
        return {"local_id": mod_dir.name, "parse_status": "INVALID", "detail": str(exc), "declared_dependencies": []}
    local_id = str(raw.get("id", mod_dir.name))
    dependencies = raw.get("dependencies") or raw.get("requiredDependencies") or []
    return {
        "local_id": local_id,
        "parse_status": parse_status_on_success,
        "detail": None,
        "declared_dependencies": [str(item) for item in dependencies] if isinstance(dependencies, list) else [],
    }


def _mod_metadata(mod_dir: Path, root: Path, result: FingerprintResult) -> dict[str, Any]:
    info_path = mod_dir / "mod_info.json"
    if info_path.is_file():
        return _parse_mod_info_file(info_path, root, result, mod_dir, parse_status_on_success="OK")

    # A mod manager convention: renaming `mod_info.json` to
    # `mod_info.json.disabled` makes Starsector itself not recognize the
    # directory as a mod at all. This is an intentional, evidenced state,
    # not corruption -- observed directly on a real installation where
    # several mods were disabled this way. Report it as such rather than
    # as a generic "missing metadata" error.
    disabled_info_path = mod_dir / "mod_info.json.disabled"
    if disabled_info_path.is_file():
        metadata = _parse_mod_info_file(disabled_info_path, root, result, mod_dir, parse_status_on_success="DISABLED")
        if metadata is not None and metadata["parse_status"] == "DISABLED":
            result.add(
                id="mod-disabled-via-rename",
                category="mod-inventory",
                severity="info",
                confidence="DETERMINISTIC",
                explanation="mod_info.json is renamed to mod_info.json.disabled, a mod-manager convention for disabling a mod; Starsector itself will not recognize this directory as a mod while it stays renamed.",
                file=_relative(root, disabled_info_path),
            )
        return metadata

    result.add(
        id="missing-mod-info",
        category="mod-inventory",
        severity="high",
        confidence="DETERMINISTIC",
        explanation="Neither mod_info.json nor mod_info.json.disabled was found for this directory; it may not be a mod at all (e.g. a cache directory or an unrelated tool installed alongside mods).",
        file=_relative(root, mod_dir),
    )
    return {"local_id": mod_dir.name, "parse_status": "MISSING", "detail": None, "declared_dependencies": []}


def build_inventory(installation_path: Path, result: FingerprintResult) -> None:
    """Populate `result.mods` and `result.enabled_mod_order`, read-only and tolerant.

    Malformed or missing metadata is preserved as a finding with raw
    location evidence; the mod directory is never discarded or given an
    invented identity.
    """

    mods_dir = installation_path / "mods"
    if not mods_dir.is_dir():
        result.add(
            id="missing-mods-directory",
            category="mod-inventory",
            severity="high",
            confidence="DETERMINISTIC",
            explanation="No `mods` directory was found under the selected installation path.",
        )
        return

    enabled = _read_enabled_mods(mods_dir, result)
    result.enabled_mod_order = enabled

    discovered_ids: set[str] = set()
    ids_to_roots: dict[str, list[str]] = {}
    for mod_dir in sorted(path for path in mods_dir.iterdir() if path.is_dir()):
        metadata = _mod_metadata(mod_dir, installation_path, result)
        discovered_ids.add(metadata["local_id"])
        relative_root = _relative(installation_path, mod_dir)
        ids_to_roots.setdefault(metadata["local_id"], []).append(relative_root)
        jar_paths = sorted(_relative(installation_path, jar) for jar in mod_dir.rglob("*.jar"))
        # A directory disabled via the mod_info.json.disabled rename is
        # never actually enabled, regardless of what enabled_mods.json
        # says for its id: that rename is stronger, more direct evidence
        # than a list that may be stale (observed on a real installation
        # where an old disabled copy and a newer enabled copy shared the
        # same declared id).
        if metadata["parse_status"] == "DISABLED":
            is_enabled: bool | None = False
        else:
            is_enabled = (metadata["local_id"] in enabled) if enabled is not None else None
        result.mods.append(
            {
                "local_id": metadata["local_id"],
                "relative_root": relative_root,
                "metadata_parse_status": metadata["parse_status"],
                "metadata_detail": metadata["detail"],
                "declared_dependencies": metadata["declared_dependencies"],
                "jar_paths": jar_paths,
                "enabled": is_enabled,
            }
        )

    for local_id, roots in ids_to_roots.items():
        if len(roots) > 1:
            result.add(
                id="duplicate-mod-id",
                category="mod-inventory",
                severity="medium",
                confidence="DETERMINISTIC",
                explanation=f"'{local_id}' is declared by more than one mod directory; jar ownership below cannot distinguish between them since it is keyed by this id.",
                evidence=roots,
            )

    if enabled is not None:
        for mod_id in enabled:
            if mod_id not in discovered_ids:
                result.add(
                    id="enabled-mod-not-found",
                    category="mod-inventory",
                    severity="high",
                    confidence="DETERMINISTIC",
                    explanation=f"'{mod_id}' is listed in enabled_mods.json but no matching mod directory/id was discovered.",
                    evidence=[mod_id],
                )

    _check_tick_marker_mod(result)


def _check_tick_marker_mod(result: FingerprintResult) -> None:
    """Proactively surface whether the optional SPW Tick Marker mod (`spw/agent-mod`) is usable.

    Without this, a user only discovers it's missing after already
    running a `DEEP_DIAGNOSTIC` capture and getting an empty
    `tick-analysis.json` -- this turns that into an actionable finding at
    inventory time, before capturing anything.
    """

    tick_marker = next((mod for mod in result.mods if mod["local_id"] == TICK_MARKER_MOD_ID), None)
    if tick_marker is None:
        result.add(
            id="tick-marker-mod-not-installed",
            category="mod-inventory",
            severity="info",
            confidence="DETERMINISTIC",
            explanation=(
                "The optional SPW Tick Marker mod (spw/agent-mod) is not installed. DEEP_DIAGNOSTIC captures "
                "will not include com.spw.TickBoundary events without it; this is not a problem unless you "
                "want tick-level analysis."
            ),
        )
    elif tick_marker["enabled"] is False:
        result.add(
            id="tick-marker-mod-not-enabled",
            category="mod-inventory",
            severity="info",
            confidence="DETERMINISTIC",
            explanation="The SPW Tick Marker mod is installed but not enabled; enable it to get tick-boundary data from a DEEP_DIAGNOSTIC capture.",
            file=tick_marker["relative_root"],
        )
