from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

CACHE_SCHEMA_VERSION = 1


def default_cache_path() -> Path:
    """A stable, per-user location so the cache survives across different `--output` directories.

    This is local-machine scratch space only: never shipped, never
    containing game/mod content itself, just a jar-hash-to-class-index
    map. Safe to delete at any time; a miss just means a normal fresh scan.
    """

    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME") or str(Path.home())
    return Path(base) / "spw" / "jar-index-cache.json"


def hash_file(path: Path) -> str | None:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def load_cache(cache_path: Path) -> dict[str, dict[str, Any]]:
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict) or data.get("schema_version") != CACHE_SCHEMA_VERSION:
        return {}
    entries = data.get("entries")
    return entries if isinstance(entries, dict) else {}


def save_cache(cache_path: Path, entries: dict[str, dict[str, Any]]) -> None:
    """Best-effort write; a failure here must never break a scan since the cache is purely disposable."""

    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps({"schema_version": CACHE_SCHEMA_VERSION, "entries": entries}), encoding="utf-8")
    except OSError:
        pass
