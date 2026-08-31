from __future__ import annotations

import re
from typing import Any

_JAVA_MAJOR = re.compile(r"^(\d+)")

MATERIAL_VARIABLES = (
    "java_major_version",
    "jvm_arguments",
    "enabled_mod_set_or_order",
    "fast_rendering_state",
    "core_integrity_state",
    "capture_settings",
    "gpu_driver_state",
)


def _java_major(environment: dict[str, Any]) -> int | None:
    """Prefer the actual target JVM's version over the tooling JDK's.

    `target_jvm` is only populated after an attach-mode capture (via
    `jcmd <pid> VM.version`, run against the profiled process itself); it
    can differ from `java` (the tooling JDK fingerprinted from `--java`) --
    e.g. a Mikohime-managed Java 27/28 setup attached to from a different
    JDK. Falling back to `java` keeps inventory-only snapshots (no capture,
    so no target JVM to query) working as before.
    """

    version = (environment.get("target_jvm") or {}).get("jdk_version") or (environment.get("java") or {}).get("java_version") or ""
    match = _JAVA_MAJOR.match(version)
    return int(match.group(1)) if match else None


def _capability_state(capabilities: list[dict[str, Any]], capability_id: str) -> str:
    for capability in capabilities or []:
        if capability.get("id") == capability_id:
            return capability.get("state", "NOT_DETECTED")
    return "NOT_DETECTED"


def _gpu_driver_state(environment: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    gpus = (environment.get("gpus") or {}).get("gpus") or []
    return tuple(sorted((gpu.get("name", "UNKNOWN"), gpu.get("driver_version", "UNKNOWN")) for gpu in gpus))


def _variable_values(snapshot: dict[str, Any]) -> dict[str, Any]:
    environment = snapshot.get("environment") or {}
    return {
        "java_major_version": _java_major(environment),
        "jvm_arguments": tuple(sorted(environment.get("configured_jvm_arguments") or [])),
        "enabled_mod_set_or_order": tuple(environment.get("enabled_mod_order") or []),
        "fast_rendering_state": _capability_state(snapshot.get("runtime_capabilities") or [], "FastRendering"),
        "core_integrity_state": (snapshot.get("core_integrity") or {}).get("status"),
        "capture_settings": snapshot.get("capture_level"),
        "gpu_driver_state": _gpu_driver_state(environment),
    }


def compute_comparability(snapshot_a: dict[str, Any], snapshot_b: dict[str, Any], declared_experiment_variable: str | None = None) -> dict[str, Any]:
    """Apply the design's comparability gate to two environment snapshots.

    Each snapshot is `{"environment": environment.json contents,
    "core_integrity": core-integrity.json contents, "runtime_capabilities":
    runtime-capabilities.json's "capabilities" list, "capture_level": str |
    None}`. `declared_experiment_variable` names a single material variable
    the user explicitly changed on purpose (an A/B experiment); when that
    is the *only* changed variable, the result is still `PARTIALLY_CONTROLLED`
    but labeled as a declared experiment rather than an uncontrolled change.
    """

    values_a = _variable_values(snapshot_a)
    values_b = _variable_values(snapshot_b)
    changed = [name for name in MATERIAL_VARIABLES if values_a[name] != values_b[name]]

    if not changed:
        return {"result": "COMPARABLE", "changed_variables": [], "is_declared_experiment": False}

    if len(changed) == 1:
        is_declared = changed[0] == declared_experiment_variable
        return {"result": "PARTIALLY_CONTROLLED", "changed_variables": changed, "is_declared_experiment": is_declared}

    proposed_matrix = None
    if len(changed) == 2:
        first, second = changed
        proposed_matrix = [
            {first: values_a[first], second: values_a[second]},
            {first: values_a[first], second: values_b[second]},
            {first: values_b[first], second: values_a[second]},
            {first: values_b[first], second: values_b[second]},
        ]

    return {
        "result": "NOT_COMPARABLE",
        "changed_variables": changed,
        "is_declared_experiment": False,
        "explanation": "Causal attribution is not possible with more than one material variable changed at once.",
        "proposed_controlled_run_matrix": proposed_matrix,
    }
