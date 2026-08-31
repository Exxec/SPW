from __future__ import annotations

from pathlib import Path

LEVELS = ("PASSIVE", "STANDARD", "DEEP_DIAGNOSTIC")
_JFC_DIR = Path(__file__).parent / "jfc"


def jfc_path(level: str) -> Path:
    """Resolve the bundled JFR configuration file for a capture level.

    `PASSIVE` is a small, explicit SPW-authored profile with no method
    sampling or allocation profiling. `STANDARD` and `DEEP_DIAGNOSTIC` are
    the JDK's own `default.jfc`/`profile.jfc`, used unmodified.
    """

    if level not in LEVELS:
        raise ValueError(f"Unknown capture level: {level!r}; expected one of {LEVELS}")
    path = _JFC_DIR / f"{level}.jfc"
    if not path.is_file():
        raise FileNotFoundError(f"Bundled JFR configuration missing for level {level!r}: {path}")
    return path
