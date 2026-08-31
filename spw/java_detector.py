from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

RELEASE_FIELDS = (
    "IMPLEMENTOR",
    "IMPLEMENTOR_VERSION",
    "JAVA_RUNTIME_VERSION",
    "JAVA_VERSION",
    "JVM_VARIANT",
    "OS_ARCH",
    "OS_NAME",
    "IMAGE_TYPE",
)

_VERSION_LINE = re.compile(r'version "([^"]+)"')


def _parse_release_file(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        if "=" not in line:
            continue
        key, _, raw_value = line.partition("=")
        key = key.strip()
        if key not in RELEASE_FIELDS:
            continue
        values[key] = raw_value.strip().strip('"')
    return values


def _release_file_path(executable: Path) -> Path:
    # `<JAVA_HOME>/bin/java(.exe)` -> `<JAVA_HOME>/release`
    return executable.parent.parent / "release"


def _run_version_output(executable: Path) -> str | None:
    try:
        completed = subprocess.run(
            [str(executable), "-version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return completed.stderr or completed.stdout or None


def detect_java(executable: Path, allow_execute: bool = True) -> dict[str, Any]:
    """Fingerprint the actual JVM behind `executable`.

    Prefers the JDK's own `release` file over any folder-name or PATH
    assumption; falls back to explicit `-version` output only when the
    release file is unavailable or unreadable.
    """

    executable = executable.expanduser()
    info: dict[str, Any] = {
        "executable": str(executable),
        "implementor": "UNKNOWN",
        "implementor_version": "UNKNOWN",
        "java_runtime_version": "UNKNOWN",
        "java_version": "UNKNOWN",
        "jvm_variant": "UNKNOWN",
        "os_arch": "UNKNOWN",
        "os_name": "UNKNOWN",
        "image_type": "UNKNOWN",
        "source": "UNAVAILABLE",
    }
    if not executable.exists():
        return info

    release_path = _release_file_path(executable)
    if release_path.is_file():
        try:
            parsed = _parse_release_file(release_path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            parsed = {}
        if parsed:
            info.update(
                implementor=parsed.get("IMPLEMENTOR", info["implementor"]),
                implementor_version=parsed.get("IMPLEMENTOR_VERSION", info["implementor_version"]),
                java_runtime_version=parsed.get("JAVA_RUNTIME_VERSION", info["java_runtime_version"]),
                java_version=parsed.get("JAVA_VERSION", info["java_version"]),
                jvm_variant=parsed.get("JVM_VARIANT", info["jvm_variant"]),
                os_arch=parsed.get("OS_ARCH", info["os_arch"]),
                os_name=parsed.get("OS_NAME", info["os_name"]),
                image_type=parsed.get("IMAGE_TYPE", info["image_type"]),
                source="release-file",
            )
            return info

    if not allow_execute:
        return info

    output = _run_version_output(executable)
    if not output:
        return info
    match = _VERSION_LINE.search(output)
    if match:
        info["java_version"] = match.group(1)
    info["java_runtime_version"] = output.strip().splitlines()[0] if output.strip() else info["java_runtime_version"]
    info["source"] = "version-output"
    return info
