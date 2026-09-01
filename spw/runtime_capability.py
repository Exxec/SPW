from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

_JAVA_MAJOR = re.compile(r"^(\d+)")


@dataclass(frozen=True)
class DetectorContext:
    """The one, stable input shape every runtime-capability detector receives.

    This is the extension point the design doc's "Community/third-party
    runtime-capability detector plugin points" recommendation asks for:
    a narrow contract, not a general plugin system. A third-party
    detector is any callable matching `Detector` below; register it with
    `register_detector` before calling `detect_runtime_capabilities`
    (e.g. from a small script, or from a `.py` file loaded via
    `spw inventory --extra-detectors-dir <path>`).
    """

    installation_path: Path
    java_info: dict[str, Any]
    core_integrity: dict[str, Any]


Detector = Callable[[DetectorContext], list[dict[str, Any]]]

MIKOHIME_MARKERS = ("Miko_Rouge.bat", "Miko_Simple.txt", "Miko_Info.txt")
FAST_RENDERING_MARKERS = ("fr.bat", "fr.vmparams")
_MIKOHIME_CONFIG_KEYWORDS = {
    "memory": ("memory", "-xmx", "-xms"),
    "cpu": ("cpu", "affinity"),
    "logging": ("log",),
    "large_page": ("large page", "largepage"),
    "fast_rendering": ("fast rendering", "fastrendering", "fr.bat"),
    "resource_cache": ("resource cache", "resourcecache"),
    "prepatcher": ("prepatch",),
}


def _record(capability_id: str, state: str, version: str | None = None, evidence: list[str] | None = None, parsed_configuration: dict[str, Any] | None = None, unsupported_assumptions: list[str] | None = None) -> dict[str, Any]:
    return {
        "id": capability_id,
        "version": version,
        "state": state,
        "evidence": evidence or [],
        "parsed_configuration": parsed_configuration or {},
        "unsupported_assumptions": unsupported_assumptions or [],
    }


def _detect_base_jdk(context: DetectorContext) -> list[dict[str, Any]]:
    java_info = context.java_info
    source = java_info.get("source", "UNAVAILABLE")
    if source == "UNAVAILABLE" or not java_info:
        unknown_evidence = ["No Java executable was fingerprinted for this installation."]
        return [
            _record("VanillaJava17", "UNKNOWN", evidence=unknown_evidence),
            _record("GenericAlternateJDK", "UNKNOWN", evidence=unknown_evidence),
        ]

    version = java_info.get("java_version") or java_info.get("java_runtime_version") or ""
    match = _JAVA_MAJOR.match(version)
    major = int(match.group(1)) if match else None
    evidence = [f"java_version={version}", f"implementor={java_info.get('implementor', 'UNKNOWN')}", f"source={source}"]

    if major == 17:
        return [
            _record("VanillaJava17", "ENABLED", version=version, evidence=evidence),
            _record("GenericAlternateJDK", "NOT_DETECTED"),
        ]
    if major is None:
        return [
            _record("VanillaJava17", "UNKNOWN", evidence=evidence),
            _record("GenericAlternateJDK", "UNKNOWN", evidence=evidence),
        ]
    return [
        _record("VanillaJava17", "NOT_DETECTED"),
        _record("GenericAlternateJDK", "ENABLED", version=version, evidence=evidence, unsupported_assumptions=["Generic detection only; no named vendor-specific kit profile has been matched."]),
    ]


def _detect_mikohime(context: DetectorContext) -> list[dict[str, Any]]:
    installation_path = context.installation_path
    found = [name for name in MIKOHIME_MARKERS if (installation_path / name).is_file()]
    if not found:
        return [_record("MikohimeConfiguration", "NOT_DETECTED")]

    parsed_configuration: dict[str, bool] = {key: False for key in _MIKOHIME_CONFIG_KEYWORDS}
    for name in found:
        try:
            text = (installation_path / name).read_text(encoding="utf-8", errors="replace").lower()
        except OSError:
            continue
        for key, keywords in _MIKOHIME_CONFIG_KEYWORDS.items():
            if any(keyword in text for keyword in keywords):
                parsed_configuration[key] = True

    return [
        _record(
            "MikohimeConfiguration",
            "ENABLED",
            evidence=found,
            parsed_configuration=parsed_configuration,
            unsupported_assumptions=["Configured settings are parsed from launcher text; actual activation of Fast Rendering/resource-cache/prepatcher is confirmed only by their own detectors and core-integrity evidence, not by this configuration alone."],
        )
    ]


def _detect_fast_rendering(context: DetectorContext) -> list[dict[str, Any]]:
    installation_path = context.installation_path
    # Observed directly on a real installation: Fast Rendering ships as a
    # dedicated `fast-rendering-<version>/` folder, and activating it
    # copies its fr.bat/fr.vmparams (and its jars) directly into
    # `starsector-core/` -- the actual launch location. Evidence from all
    # three locations is kept distinct so a reader can judge for
    # themselves whether this is "installed" or "active".
    evidence: list[str] = []
    for name in FAST_RENDERING_MARKERS:
        if (installation_path / name).is_file():
            evidence.append(name)
        if (installation_path / "starsector-core" / name).is_file():
            evidence.append(f"starsector-core/{name}")

    try:
        top_level_entries = list(installation_path.iterdir())
    except OSError:
        top_level_entries = []
    for entry in top_level_entries:
        if not entry.is_dir():
            continue
        normalized = entry.name.lower().replace("_", "-").replace(" ", "-")
        if not normalized.startswith("fast-rendering"):
            continue
        for name in FAST_RENDERING_MARKERS:
            if (entry / name).is_file():
                evidence.append(f"{entry.name}/{name}")

    if not evidence:
        return [_record("FastRendering", "NOT_DETECTED")]
    return [
        _record(
            "FastRendering",
            "ENABLED",
            evidence=sorted(set(evidence)),
            unsupported_assumptions=[
                "Matches known Fast Rendering launcher file names at the installation root, inside starsector-core/, and inside any fast-rendering* staging folder; does not verify renderer-bridge behavior at runtime.",
                "Evidence found only outside starsector-core/ shows the distribution is present, not necessarily that it is the currently active core.",
            ],
        )
    ]


_FR_RESOURCE_CACHE_JAR = "fr-resource-cache-agent.jar"


def _detect_fast_rendering_resource_cache(context: DetectorContext) -> list[dict[str, Any]]:
    """Detect FR Resource Cache from its real, documented installation layout.

    Confirmed against the actual distribution (v0.3): the agent jar is
    copied into `starsector-core/` alongside `fr.jar`/`fr.agent.jar`, wired
    in via a `-javaagent:fr-resource-cache-agent.jar` line appended to
    `starsector-core/fr.vmparams`, and it maintains its own cache directory
    at the installation root (`fr-resource-cache/`, holding `resources.pack`/
    `resources.index`/`cache.lock`). Each is kept as distinct evidence so a
    reader can judge "present" versus "wired into the actual launch" versus
    "has run at least once" for themselves, the same distinction already
    made for FastRendering itself.
    """

    installation_path = context.installation_path
    evidence: list[str] = []
    if (installation_path / "starsector-core" / _FR_RESOURCE_CACHE_JAR).is_file():
        evidence.append(f"starsector-core/{_FR_RESOURCE_CACHE_JAR}")
    vmparams_path = installation_path / "starsector-core" / "fr.vmparams"
    if vmparams_path.is_file():
        try:
            text = vmparams_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        if f"-javaagent:{_FR_RESOURCE_CACHE_JAR}" in text:
            evidence.append("starsector-core/fr.vmparams (javaagent entry)")
    if (installation_path / "fr-resource-cache").is_dir():
        evidence.append("fr-resource-cache/")

    if not evidence:
        return [_record("FastRenderingResourceCache", "NOT_DETECTED")]
    return [
        _record(
            "FastRenderingResourceCache",
            "ENABLED",
            evidence=sorted(set(evidence)),
            unsupported_assumptions=["Confirms the agent jar and/or its javaagent wiring and/or its cache directory are present; does not confirm the `[FR Resource Cache]` console messages the real tool prints actually appeared at runtime."],
        )
    ]


_PREPATCHER_MOD_ID = "starsector_prepatcher"
_PREPATCHER_AGENT_JAR_NAME = "StarsectorPrepatcherAgent.jar"


def _detect_prepatcher(context: DetectorContext) -> list[dict[str, Any]]:
    """Detect StarsectorPrepatcher from its real, documented installation layout.

    Confirmed against the actual distribution (v0.18.4): unlike Fast
    Rendering, this installs as an ordinary mod under `mods/` (its own
    `mod_info.json` declares id `starsector_prepatcher`) -- a root-level
    folder-name heuristic would never find it there. It additionally
    requires a `-javaagent:.../StarsectorPrepatcherAgent.jar` line in
    `vmparams` (or `fr.vmparams`, if combined with Fast Rendering) to
    actually take effect; "installed as a mod" and "wired into the launch"
    are kept as distinct evidence rather than conflated.
    """

    installation_path = context.installation_path
    evidence: list[str] = []
    mods_dir = installation_path / "mods"
    try:
        mod_dirs = [path for path in mods_dir.iterdir() if path.is_dir()] if mods_dir.is_dir() else []
    except OSError:
        mod_dirs = []
    for mod_dir in mod_dirs:
        for info_name in ("mod_info.json", "mod_info.json.disabled"):
            info_path = mod_dir / info_name
            if not info_path.is_file():
                continue
            try:
                declared = json.loads(info_path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                continue
            if isinstance(declared, dict) and declared.get("id") == _PREPATCHER_MOD_ID:
                evidence.append(f"mods/{mod_dir.name}/{info_name}")

    for vmparams_name in ("vmparams", "vmparams.txt", "starsector-core/fr.vmparams"):
        vmparams_path = installation_path / vmparams_name
        if not vmparams_path.is_file():
            continue
        try:
            text = vmparams_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if _PREPATCHER_AGENT_JAR_NAME in text:
            evidence.append(f"{vmparams_name} (javaagent entry)")

    if not evidence:
        return [_record("StarsectorPrepatcher", "NOT_DETECTED")]
    return [
        _record(
            "StarsectorPrepatcher",
            "ENABLED",
            evidence=sorted(set(evidence)),
            unsupported_assumptions=["Confirms the mod is installed under mods/ and/or the javaagent is wired into vmparams; does not confirm the agent successfully patched anything at runtime (the game's own log carries Prepatcher's patch-status lines for that)."],
        )
    ]


def _detect_unknown_modified_runtime(context: DetectorContext) -> list[dict[str, Any]]:
    core_integrity = context.core_integrity
    file_status = core_integrity.get("file_status", {}) if core_integrity else {}
    unknown_files = [relative for relative, status in file_status.items() if status == "UNKNOWN_DIFFERENCE"]
    if not unknown_files:
        return [_record("UnknownModifiedRuntime", "NOT_DETECTED")]
    return [
        _record(
            "UnknownModifiedRuntime",
            "ENABLED",
            evidence=unknown_files,
            unsupported_assumptions=["Added because core-integrity found differences from baseline with no matching known-modification fingerprint; this is not itself an identification of what changed."],
        )
    ]


_BUILTIN_DETECTORS: tuple[Detector, ...] = (
    _detect_base_jdk,
    _detect_mikohime,
    _detect_fast_rendering,
    _detect_fast_rendering_resource_cache,
    _detect_prepatcher,
    _detect_unknown_modified_runtime,
)

# The extension point itself: a third party adds a detector by calling
# `register_detector` with any callable matching `Detector` (takes one
# `DetectorContext`, returns a list of capability records built with the
# same shape `_record` produces -- id/version/state/evidence/
# parsed_configuration/unsupported_assumptions). No dynamic-loading
# machinery lives here on purpose, matching the "generic first, named
# adapters only with real evidence" discipline: `spw inventory
# --extra-detectors-dir <path>` (cli.py) is the only place `.py` files
# get imported, and only from a directory the user explicitly names.
_detector_registry: list[Detector] = list(_BUILTIN_DETECTORS)


def register_detector(detector: Detector) -> None:
    """Add a third-party runtime-capability detector, run alongside the built-in ones."""

    _detector_registry.append(detector)


def unregister_detector(detector: Detector) -> None:
    """Remove a previously registered detector; no-op if it was never registered."""

    try:
        _detector_registry.remove(detector)
    except ValueError:
        pass


def reset_detector_registry() -> None:
    """Restore the registry to just the built-in detectors (mainly for tests)."""

    _detector_registry[:] = list(_BUILTIN_DETECTORS)


def detect_runtime_capabilities(installation_path: Path, java_info: dict[str, Any], core_integrity: dict[str, Any]) -> list[dict[str, Any]]:
    """Detect the composable runtime-capability layers present in an installation.

    Capabilities are independent and can co-occur (design doc, "Runtime
    capability stack"): a base-JDK label, zero or more launcher-layer
    capabilities (Mikohime, Fast Rendering, its resource cache, the
    prepatcher), and `UnknownModifiedRuntime` whenever core-integrity found
    a difference no other capability's evidence explains -- plus whatever
    any registered third-party detector adds.

    A detector that raises is skipped with an `UNKNOWN`-state placeholder
    record rather than aborting the whole scan: one broken third-party
    detector must not take down inventory for everything else.
    """

    context = DetectorContext(installation_path=installation_path, java_info=java_info, core_integrity=core_integrity)
    capabilities: list[dict[str, Any]] = []
    for detector in _detector_registry:
        try:
            capabilities.extend(detector(context))
        except Exception as exc:  # noqa: BLE001 -- a third-party detector's failure mode is unknown by design
            capabilities.append(
                _record(
                    getattr(detector, "__name__", "unknown_detector"),
                    "UNKNOWN",
                    evidence=[f"Detector raised {type(exc).__name__}: {exc}"],
                    unsupported_assumptions=["This detector failed to run; its capability state could not be determined."],
                )
            )
    return capabilities
