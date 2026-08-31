from __future__ import annotations

from typing import Any


def record_scenario_from_capture_descriptor(descriptor: dict[str, Any], name: str, description: str | None = None) -> dict[str, Any]:
    """Turn an observed launch-mode capture into a reusable `spw benchmark` scenario manifest.

    This is deliberately narrower than "record an actual play session's
    reproducible inputs (seed, settings, scripted sequence)" as originally
    proposed: capturing real keystroke/mouse input sequences or an RNG
    seed would need a deeper in-game hook (like the SPW Tick Marker mod,
    but for input recording) that does not exist yet, and guessing at one
    risks a manifest that silently does not reproduce what it claims to.
    What is achievable and genuinely useful without that: turning the
    exact launch command a `spw capture`/`spw benchmark` run already used
    -- recorded in its `capture-descriptor.json` -- into a manifest someone
    else (or a later run) can replay via `spw benchmark`, removing the
    barrier of hand-authoring one field-by-field.

    Raises `ValueError` for an attach-mode descriptor, since there is no
    launch command to recover from attaching to an already-running process.
    """

    if descriptor.get("capture_type") != "launch":
        raise ValueError("Only a launch-mode capture descriptor has a launch command to record a scenario from; this one is attach-mode.")

    settings = descriptor.get("settings") or {}
    launch_args = settings.get("launch_args")
    if not launch_args:
        raise ValueError("Capture descriptor has no launch_args to record.")

    manifest: dict[str, Any] = {
        "name": name,
        "description": description or f"Recorded from an observed launch-mode capture ({descriptor.get('target', 'unknown java executable')}).",
        "java_executable": descriptor.get("target"),
        "launch_args": launch_args,
        "duration_seconds": settings.get("duration_seconds"),
        "level": descriptor.get("level"),
        "recorded_from": "capture-descriptor.json",
        "limitations": (
            "Recorded from an observed launch command only -- it replays the same executable, arguments, and "
            "capture settings, not player inputs, campaign seed, or save state. Two runs of this manifest are "
            "not guaranteed to be the same in-game scenario unless the launch_args already pin that (e.g. a "
            "fixed save file)."
        ),
    }
    return manifest
