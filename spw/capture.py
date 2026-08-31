from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path

from .capture_levels import jfc_path
from .java_detector import detect_java
from .models import FingerprintResult
from .target_jvm import detect_target_jvm

RECORDING_NAME = "spw"
DEFAULT_LEVEL = "STANDARD"


def _jcmd_path(java_executable: Path | None, explicit_jcmd: Path | None) -> Path | None:
    if explicit_jcmd is not None:
        return explicit_jcmd
    if java_executable is not None:
        suffix = java_executable.suffix
        candidate = java_executable.with_name(f"jcmd{suffix}")
        if candidate.exists():
            return candidate
    return None


def _java_path_from_jcmd(jcmd_path: Path) -> Path:
    return jcmd_path.with_name(f"java{jcmd_path.suffix}")


_JAVA_MAJOR = re.compile(r"^(\d+)")


def _java_major_version(version: str | None) -> int | None:
    if not version:
        return None
    match = _JAVA_MAJOR.match(version)
    return int(match.group(1)) if match else None


def build_attach_start_command(jcmd_path: Path, pid: int, output_file: Path, duration_seconds: int, level: str = DEFAULT_LEVEL) -> list[str]:
    # jcmd forwards the whole command as one line to the target JVM's own
    # diagnostic-command parser, which re-splits on whitespace; a
    # settings/filename path containing a space (a virtual certainty on
    # Windows) must carry its own literal quote characters to survive that
    # re-split, even though each is already a single, correctly separated
    # argv token on the client side.
    return [
        str(jcmd_path),
        str(pid),
        "JFR.start",
        f"name={RECORDING_NAME}",
        f'settings="{jfc_path(level)}"',
        f"duration={duration_seconds}s",
        f'filename="{output_file}"',
    ]


def build_attach_stop_command(jcmd_path: Path, pid: int) -> list[str]:
    return [str(jcmd_path), str(pid), "JFR.stop", f"name={RECORDING_NAME}"]


def build_launch_command(java_executable: Path, launch_args: list[str], output_file: Path, duration_seconds: int, level: str = DEFAULT_LEVEL) -> list[str]:
    """Insert a `-XX:StartFlightRecording` flag ahead of the caller's own explicit launch arguments.

    `launch_args` is the user's own explicit command tail (e.g. `-jar
    starfarer.jar`); SPW never derives or rewrites it from installation
    files on its own.
    """

    flight_recording_flag = f"-XX:StartFlightRecording=name={RECORDING_NAME},settings={jfc_path(level)},duration={duration_seconds}s,filename={output_file}"
    return [str(java_executable), flight_recording_flag, *launch_args]


def run_attach_capture(
    result: FingerprintResult,
    pid: int,
    output_file: Path,
    duration_seconds: int,
    level: str = DEFAULT_LEVEL,
    jcmd_path: Path | None = None,
    java_executable: Path | None = None,
) -> dict[str, object]:
    """Start a JFR recording against an already-running JVM via `jcmd` attach, and wait for it to finish.

    `--java`/`--jcmd` name the *tooling* JDK used to talk to the target
    process, not necessarily the JVM the target is actually running under
    (a real gap for e.g. a Mikohime-managed Java 27/28 setup attached to
    from a different JDK on the operator's PATH). The descriptor therefore
    records both `tooling_jdk` (from the executable used to invoke jcmd)
    and `target_jvm` (from `jcmd <pid> VM.version`, answered by the target
    process itself) as distinct fields, so a report never silently assumes
    they are the same JVM.
    """

    resolved_jcmd = _jcmd_path(java_executable, jcmd_path)
    descriptor: dict[str, object] = {
        "capture_type": "attach",
        "target": f"pid:{pid}",
        "level": level,
        "settings": {"duration_seconds": duration_seconds, "output_file": str(output_file)},
        "output_path": None,
        "incomplete_reason": None,
        "tooling_jdk": None,
        "target_jvm": None,
    }
    if resolved_jcmd is None:
        descriptor["incomplete_reason"] = "jcmd was not found; supply --jcmd explicitly or an accompanying java executable."
        result.add(
            id="jcmd-unavailable",
            category="capture",
            severity="high",
            confidence="DETERMINISTIC",
            explanation="No jcmd executable could be located for JFR attach.",
        )
        return descriptor

    tooling_java_path = java_executable if java_executable is not None else _java_path_from_jcmd(resolved_jcmd)
    descriptor["tooling_jdk"] = detect_java(tooling_java_path, allow_execute=True)
    descriptor["target_jvm"] = detect_target_jvm(resolved_jcmd, pid)
    tooling_major = _java_major_version(descriptor["tooling_jdk"].get("java_version"))
    target_major = _java_major_version(descriptor["target_jvm"].get("jdk_version"))
    if tooling_major is not None and target_major is not None and tooling_major != target_major:
        result.add(
            id="tooling-jdk-target-jvm-version-mismatch",
            category="capture",
            severity="info",
            confidence="DETERMINISTIC",
            explanation=(
                f"The tooling JDK used to attach (major version {tooling_major}) differs from the target "
                f"JVM actually running the profiled process (major version {target_major}). This is expected "
                "for setups like Mikohime that run Starsector under a different Java version than the "
                "operator's own tooling -- it is reported for awareness, not as a problem."
            ),
            evidence=[
                f"tooling_jdk: {descriptor['tooling_jdk'].get('implementor', 'UNKNOWN')} {descriptor['tooling_jdk'].get('java_version', 'UNKNOWN')}",
                f"target_jvm: {descriptor['target_jvm'].get('vm_name', 'UNKNOWN')} {descriptor['target_jvm'].get('vm_version', 'UNKNOWN')}",
            ],
        )

    start_command = build_attach_start_command(resolved_jcmd, pid, output_file, duration_seconds, level=level)
    start_output = ""
    try:
        start_completed = subprocess.run(start_command, capture_output=True, text=True, timeout=15, check=True)
        start_output = (start_completed.stdout or "").strip()
    except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
        descriptor["incomplete_reason"] = f"JFR.start failed: {exc}"
        result.add(
            id="jfr-start-failed",
            category="capture",
            severity="high",
            confidence="DETERMINISTIC",
            explanation=str(exc),
        )
        return descriptor

    # `JFR.start ... duration=Ns` returns immediately; the recording and its
    # dump-to-file happen asynchronously inside the target JVM. Wait out the
    # duration, then poll briefly for the file to appear and stop growing --
    # returning right after JFR.start would race a still-empty or
    # still-being-written recording.
    time.sleep(duration_seconds)
    if not _wait_for_stable_file(output_file, max_wait_seconds=30):
        # jcmd forwards a rejected diagnostic command to the target JVM's
        # own handler, which can print an explanation and still exit 0 --
        # confirmed directly against a real JRE 8 process with JFR not
        # commercially unlocked ("Java Flight Recorder not enabled. Use
        # VM.unlock_commercial_features to enable."), so `check=True`
        # above never raises for it. That text, when present, is a far
        # more actionable reason than the generic file-not-ready message
        # alone, so it is appended rather than discarded.
        descriptor["incomplete_reason"] = "The recording file did not appear (or was still being written) within the expected time after the capture duration elapsed."
        if start_output:
            descriptor["incomplete_reason"] += f" jcmd JFR.start output: {start_output}"
        result.add(
            id="capture-file-not-ready",
            category="capture",
            severity="medium",
            confidence="DETERMINISTIC",
            explanation=descriptor["incomplete_reason"],
        )
        return descriptor

    descriptor["output_path"] = str(output_file)
    return descriptor


def _wait_for_stable_file(path: Path, max_wait_seconds: int, poll_interval_seconds: float = 0.5) -> bool:
    """Poll until `path` exists and its size is unchanged across two consecutive checks."""

    deadline = time.monotonic() + max_wait_seconds
    last_size = -1
    while time.monotonic() < deadline:
        if path.is_file():
            size = path.stat().st_size
            if size > 0 and size == last_size:
                return True
            last_size = size
        time.sleep(poll_interval_seconds)
    return path.is_file() and path.stat().st_size > 0


def run_launch_capture(
    result: FingerprintResult,
    java_executable: Path,
    launch_args: list[str],
    output_file: Path,
    duration_seconds: int,
    level: str = DEFAULT_LEVEL,
) -> dict[str, object]:
    """Spawn a new JVM with an explicit launch command plus an appended JFR flag, and wait out the capture duration.

    `duration=Ns` only bounds the *recording*, not the launched process --
    a real game keeps running long after its profile finishes writing.
    This therefore does not wait for the process to exit (that could block
    indefinitely on a real game session); it waits out the capture window
    and polls for the recording file exactly as attach mode does, leaving
    the launched process running on its own for the user to close.
    """

    descriptor: dict[str, object] = {
        "capture_type": "launch",
        "target": str(java_executable),
        "level": level,
        "settings": {"duration_seconds": duration_seconds, "output_file": str(output_file), "launch_args": launch_args},
        "output_path": None,
        "incomplete_reason": None,
    }
    command = build_launch_command(java_executable, launch_args, output_file, duration_seconds, level=level)
    try:
        subprocess.Popen(command)
    except OSError as exc:
        descriptor["incomplete_reason"] = f"Launch failed: {exc}"
        result.add(
            id="capture-launch-failed",
            category="capture",
            severity="high",
            confidence="DETERMINISTIC",
            explanation=str(exc),
        )
        return descriptor

    time.sleep(duration_seconds)
    if not _wait_for_stable_file(output_file, max_wait_seconds=30):
        descriptor["incomplete_reason"] = "The recording file did not appear (or was still being written) within the expected time after the capture duration elapsed; the launched process is left running."
        result.add(
            id="capture-produced-no-recording",
            category="capture",
            severity="high",
            confidence="DETERMINISTIC",
            explanation=descriptor["incomplete_reason"],
        )
        return descriptor

    descriptor["output_path"] = str(output_file)
    return descriptor
