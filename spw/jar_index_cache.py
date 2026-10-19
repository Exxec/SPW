from __future__ import annotations

import hashlib
import json
import os
import tempfile
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
    except (OSError, ValueError, UnicodeError):
        return {}
    if not isinstance(data, dict) or data.get("schema_version") != CACHE_SCHEMA_VERSION:
        return {}
    entries = data.get("entries")
    if not isinstance(entries, dict):
        return {}
    # Treat each malformed entry as a cache miss without discarding valid peers.
    return {
        digest: entry
        for digest, entry in entries.items()
        if isinstance(entry, dict)
        and all(
            isinstance(entry.get(field), list)
            and all(isinstance(item, str) for item in entry[field])
            for field in ("prefixes", "classes")
        )
    }


def save_cache(cache_path: Path, entries: dict[str, dict[str, Any]]) -> None:
    """Best-effort write; a failure here must never break a scan since the cache is purely disposable."""

    temporary_path: Path | None = None
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        # A sibling file keeps replacement on the same filesystem. Close it
        # before replacing so this also works on Windows.
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=cache_path.parent,
            prefix=f".{cache_path.name}.", suffix=".tmp", delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump({"schema_version": CACHE_SCHEMA_VERSION, "entries": entries}, handle)
        os.replace(temporary_path, cache_path)
    except OSError:
        pass
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
