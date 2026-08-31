from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_PID_TID_PATTERN = re.compile(r"pid=(\d+),\s*tid=(\d+)")
_ELAPSED_SECONDS_PATTERN = re.compile(r"elapsed time:\s*([0-9.]+)\s*seconds")
_JAVA_FRAMES_HEADER = "Java frames:"
_KEY_LINES = {
    "JRE version:": "jre_version",
    "Java VM:": "java_vm",
    "Command Line:": "command_line",
    "Host:": "host",
    "Time:": "crash_time_raw",
}


def find_crash_logs(directory: Path) -> list[Path]:
    """Find `hs_err_pid*.log` files directly in `directory` (non-recursive).

    The JVM writes these to its working directory by default -- normally
    the Starsector installation root for a game launched the ordinary way
    -- so a non-recursive, top-level-only search matches where they
    actually land without a slow, noisy recursive scan of the whole
    installation (mods/ especially).
    """

    try:
        return sorted(directory.glob("hs_err_pid*.log"))
    except OSError:
        return []


def parse_crash_log(path: Path) -> dict[str, Any]:
    """Extract the small, stable, high-value fields from a JVM fatal-error log.

    `hs_err_pid*.log` is a large, sprawling, crash-type-dependent format
    (native SIGSEGV vs. an internal VM assertion vs. an OOM-triggered
    crash all format their problem header differently); this deliberately
    extracts only what stays stable across crash types -- pid/tid, the raw
    `#`-prefixed problem-summary lines (evidence, not a specific parsed
    cause), the handful of `Key: value` lines every hs_err file carries,
    and the Java-frame stack at the point of the crash -- rather than
    attempting to fully model the format.
    """

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {"path": str(path), "parse_status": "UNREADABLE"}

    result: dict[str, Any] = {
        "path": str(path),
        "parse_status": "PARSED",
        "pid": None,
        "tid": None,
        "problem_summary": [],
        "jre_version": None,
        "java_vm": None,
        "command_line": None,
        "host": None,
        "crash_time_raw": None,
        "elapsed_seconds": None,
        "java_frames": [],
    }

    pid_tid_match = _PID_TID_PATTERN.search(text)
    if pid_tid_match:
        result["pid"] = int(pid_tid_match.group(1))
        result["tid"] = int(pid_tid_match.group(2))

    lines = text.splitlines()

    in_problem_block = False
    for line in lines:
        if "A fatal error has been detected" in line:
            in_problem_block = True
            continue
        if not in_problem_block:
            continue
        stripped = line.strip()
        if not stripped.startswith("#"):
            break
        content = stripped.lstrip("#").strip()
        if content:
            result["problem_summary"].append(content)

    for line in lines:
        # `JRE version:`/`Java VM:` are inside the boxed "#" header;
        # `Command Line:`/`Host:`/`Time:` (in the SUMMARY section further
        # down) are not -- stripping a leading "#" handles both since it's
        # a no-op where there isn't one.
        unboxed = line.strip().lstrip("#").strip()
        for prefix, key in _KEY_LINES.items():
            if unboxed.startswith(prefix):
                result[key] = unboxed[len(prefix) :].strip()
                if key == "crash_time_raw":
                    elapsed_match = _ELAPSED_SECONDS_PATTERN.search(unboxed)
                    if elapsed_match:
                        result["elapsed_seconds"] = float(elapsed_match.group(1))

    in_java_frames = False
    for line in lines:
        if line.strip().startswith(_JAVA_FRAMES_HEADER):
            in_java_frames = True
            continue
        if in_java_frames:
            stripped = line.strip()
            if not stripped or stripped[0] not in "jJvV":
                break
            result["java_frames"].append(stripped)

    return result
