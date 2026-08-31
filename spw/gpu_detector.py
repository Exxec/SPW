from __future__ import annotations

import json
import platform
import subprocess
from typing import Any


def detect_gpus(allow_execute: bool = True) -> dict[str, Any]:
    """Detect installed GPUs and driver versions via the OS's own hardware inventory.

    Never guesses which GPU an application actually renders on (a laptop
    with hybrid Intel/NVIDIA graphics, observed directly during
    development, has no reliable way to tell from here) -- every detected
    GPU is reported, and a reader/the comparability gate can see all of
    them change. Windows-only for now (queries the `Win32_VideoController`
    CIM class via PowerShell); other platforms report `source:
    "UNAVAILABLE"` rather than fabricating a result.
    """

    result: dict[str, Any] = {"gpus": [], "source": "UNAVAILABLE"}
    if platform.system() != "Windows" or not allow_execute:
        return result

    command = [
        "powershell",
        "-NoProfile",
        "-Command",
        "Get-CimInstance Win32_VideoController | Select-Object Name,DriverVersion | ConvertTo-Json -Compress",
    ]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return result
    if completed.returncode != 0 or not completed.stdout.strip():
        return result
    try:
        data = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return result

    entries = data if isinstance(data, list) else [data]
    gpus = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = entry.get("Name")
        driver_version = entry.get("DriverVersion")
        if name or driver_version:
            gpus.append({"name": str(name) if name else "UNKNOWN", "driver_version": str(driver_version) if driver_version else "UNKNOWN"})

    if gpus:
        result["gpus"] = gpus
        result["source"] = "wmi"
    return result
