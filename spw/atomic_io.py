from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    """Replace `path` with `text` as a whole file or leave the previous file untouched.

    The text goes to a unique sibling temporary file (same directory, so the
    replace stays on one filesystem), which is flushed, fsynced and closed
    before `os.replace`. On any failure the temporary file is removed and the
    original error is re-raised: callers that want best-effort behaviour (the
    JAR-index cache) catch it themselves; required reports let it propagate.
    This prevents partial-file visibility; it does not merge simultaneous writers.
    """

    path = Path(path)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding=encoding, dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def atomic_write_json(path: Path, payload: Any) -> None:
    """Write `payload` as the project's standard indented, key-sorted JSON, atomically."""

    atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=True))
