from __future__ import annotations

import shlex
from pathlib import Path


def parse_configured_jvm_arguments(vmparams_path: Path) -> list[str]:
    """Parse a Starsector `vmparams`/`vmparams.txt` file into JVM flags.

    These are the *configured* arguments written to disk, not necessarily
    the arguments an actual running process was launched with; callers must
    not conflate the two. Only tokens starting with `-` are kept — the
    leading executable name/path is discarded.
    """

    try:
        text = vmparams_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    tokens = shlex.split(text, posix=False)
    return [token for token in tokens if token.startswith("-")]
