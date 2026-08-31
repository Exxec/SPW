from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

_VERSION_LINE = re.compile(r"^(?P<vm_name>.+?) version (?P<vm_version>\S+)\s*$")
_JDK_LINE = re.compile(r"^JDK (?P<jdk_version>\S+)\s*$")


def parse_vm_version_output(output: str) -> dict[str, str | None]:
    """Parse `jcmd <pid> VM.version` stdout into a structured record.

    Real output, confirmed on this machine against a running Temurin 25
    JVM (queried via a *different* Temurin 25 install's `jcmd`, to confirm
    the report describes the target, not the tool):

        32800:
        OpenJDK 64-Bit Server VM version 25.0.4.1+1-LTS
        JDK 25.0.4.1

    The first line is the pid echo; the second is the VM name/version
    line; the third is a plain JDK version line. Either regex failing to
    match leaves the corresponding field `None` rather than guessing --
    early-access or vendor builds (e.g. a Mikohime-managed Java 27/28
    setup) are not guaranteed to keep this exact wording.
    """

    vm_name: str | None = None
    vm_version: str | None = None
    jdk_version: str | None = None
    for raw_line in output.splitlines():
        line = raw_line.strip()
        version_match = _VERSION_LINE.match(line)
        if version_match:
            vm_name = version_match.group("vm_name")
            vm_version = version_match.group("vm_version")
            continue
        jdk_match = _JDK_LINE.match(line)
        if jdk_match:
            jdk_version = jdk_match.group("jdk_version")
    return {"vm_name": vm_name, "vm_version": vm_version, "jdk_version": jdk_version}


def detect_target_jvm(jcmd_path: Path, pid: int) -> dict[str, Any]:
    """Fingerprint the actual JVM behind a running process id via `jcmd <pid> VM.version`.

    This is deliberately independent of whichever JDK's `jcmd`/`java`
    executable was used to invoke it (the "tooling JDK", see
    `java_detector.detect_java`): `jcmd` forwards the `VM.version`
    diagnostic command to the *target* process's own JVM over its attach
    API, so the reported version is the target's, not the caller's --
    confirmed directly: querying a real Temurin-25.0.4.1 process with a
    Temurin-25.0.4.7 install's `jcmd` still correctly reports 25.0.4.1.

    This matters specifically for attach mode: `--java`/`--jcmd` locate
    the tool used to talk to the target, not necessarily the JVM
    Starsector is actually running under (e.g. a Mikohime-managed Java
    27/28 setup launched by a different JDK than the one on the
    operator's own PATH). Treating the tooling JDK's version as the
    profiled JVM's version would silently misreport the environment for
    exactly that case.
    """

    info: dict[str, Any] = {
        "pid": pid,
        "vm_name": None,
        "vm_version": None,
        "jdk_version": None,
        "source": "UNAVAILABLE",
        "limitation": None,
    }
    try:
        completed = subprocess.run(
            [str(jcmd_path), str(pid), "VM.version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        info["limitation"] = f"jcmd VM.version failed: {exc}"
        return info
    if completed.returncode != 0:
        info["limitation"] = f"jcmd VM.version failed: {(completed.stderr or completed.stdout or '').strip()}"
        return info
    info.update(parse_vm_version_output(completed.stdout))
    info["source"] = "jcmd-VM.version"
    return info
