from __future__ import annotations

import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any

from . import jar_index_cache
from .models import FingerprintResult

# Reserved pseudo-owner ids for code that is not a mod, so attribution has
# a real, evidence-backed answer for "this is the base game" or "this is
# Fast Rendering" instead of falling through to UNKNOWN. Chosen to be
# extremely unlikely to collide with a real Starsector mod id (mod ids
# observed in practice are simple identifiers, never double-underscore
# wrapped); a collision would still resolve safely as AMBIGUOUS rather
# than crash or silently misattribute.
CORE_OWNER_ID = "__starsector_core__"
FAST_RENDERING_OWNER_ID = "__fast_rendering__"


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _core_jar_owner(jar_name: str) -> str:
    return FAST_RENDERING_OWNER_ID if jar_name.lower().startswith("fr.") else CORE_OWNER_ID


def _package_prefix(class_entry_name: str, depth: int = 2) -> str | None:
    parts = class_entry_name.split("/")[:-1]
    if not parts:
        return None
    return "/".join(parts[:depth])


def _index_jar(jar_path: Path) -> tuple[set[str], set[str], str]:
    try:
        with zipfile.ZipFile(jar_path) as archive:
            prefixes: set[str] = set()
            classes: set[str] = set()
            for name in archive.namelist():
                if name.endswith(".class"):
                    classes.add(name[: -len(".class")])
                    prefix = _package_prefix(name)
                    if prefix:
                        prefixes.add(prefix)
            return prefixes, classes, "INDEXED"
    except (OSError, zipfile.BadZipFile):
        return set(), set(), "UNREADABLE"


def _index_jar_cached(jar_path: Path, cache: dict[str, dict[str, Any]], updated_cache_entries: dict[str, dict[str, Any]]) -> tuple[set[str], set[str], str]:
    """`_index_jar`, but reusing a prior run's result when the jar's content hash is unchanged.

    Only successful (`INDEXED`) results are cached; an unreadable jar is
    always retried fresh next time rather than remembered as permanently
    broken.
    """

    digest = jar_index_cache.hash_file(jar_path)
    if digest is not None and digest in cache:
        cached = cache[digest]
        return set(cached["prefixes"]), set(cached["classes"]), "INDEXED"

    prefixes, classes, status = _index_jar(jar_path)
    if digest is not None and status == "INDEXED":
        entry = {"prefixes": sorted(prefixes), "classes": sorted(classes)}
        updated_cache_entries[digest] = entry
    return prefixes, classes, status


def _index_and_accumulate(
    jar_path: Path,
    jar_relative: str,
    owner_id: str,
    result: FingerprintResult,
    prefix_owners: dict[str, set[str]],
    class_owners: dict[str, set[str]],
    entries: list[dict[str, object]],
    cache: dict[str, dict[str, Any]],
    updated_cache_entries: dict[str, dict[str, Any]],
) -> None:
    prefixes, classes, status = _index_jar_cached(jar_path, cache, updated_cache_entries)
    if status == "UNREADABLE":
        result.add(
            id="unreadable-jar",
            category="jar-ownership",
            severity="high",
            confidence="DETERMINISTIC",
            explanation="JAR could not be opened for class indexing.",
            file=jar_relative,
        )
    for prefix in prefixes:
        prefix_owners[prefix].add(owner_id)
    for class_name in classes:
        class_owners[class_name].add(owner_id)
    entries.append(
        {
            "jar_path": jar_relative,
            "owner_candidates": [owner_id],
            "package_prefixes": sorted(prefixes),
            "class_index_status": status,
            "confidence": "EXACT" if status == "INDEXED" else "UNKNOWN",
        }
    )


def build_jar_ownership(installation_path: Path, result: FingerprintResult, use_cache: bool = True, cache_path: Path | None = None) -> None:
    """Index every enabled mod's JARs and flag package prefixes shared by more than one mod.

    A JAR located inside a mod's own directory tree is owned by that mod
    (`EXACT`) at the file level. Package prefixes that appear in more than
    one mod's JARs (bundled duplicate libraries) are recorded separately as
    `shared_package_prefixes`, and any owning JAR that contributes one of
    those shared prefixes is downgraded to `AMBIGUOUS` — a runtime stack
    frame in a shared package must not be attributed to one mod without
    further evidence.

    `starsector-core/*.jar` is indexed too, under the reserved
    `CORE_OWNER_ID`/`FAST_RENDERING_OWNER_ID` pseudo-owners, in the same
    pass so a collision between a mod's bundled library and a base-game
    dependency (both `log4j`, say) is caught by the same ambiguity check
    below rather than silently misattributing base-game CPU/allocation
    samples to a mod as "unknown".

    Successfully indexed jars are cached on disk (`jar_index_cache.py`),
    keyed by content hash, so re-scanning the same, unchanged installation
    -- the common case for repeated `spw inventory`/`spw diagnose` runs --
    does not re-open and re-walk every jar's zip contents every time. Pass
    `use_cache=False` to force a fully fresh scan.
    """

    cache_path = cache_path or jar_index_cache.default_cache_path()
    cache = jar_index_cache.load_cache(cache_path) if use_cache else {}
    updated_cache_entries: dict[str, dict[str, Any]] = {}

    prefix_owners: dict[str, set[str]] = defaultdict(set)
    class_owners: dict[str, set[str]] = defaultdict(set)
    entries: list[dict[str, object]] = []

    for mod in result.mods:
        local_id = mod["local_id"]
        for jar_relative in mod["jar_paths"]:
            jar_path = installation_path / jar_relative
            _index_and_accumulate(jar_path, jar_relative, local_id, result, prefix_owners, class_owners, entries, cache, updated_cache_entries)

    core_dir = installation_path / "starsector-core"
    if core_dir.is_dir():
        try:
            core_jars = sorted(core_dir.glob("*.jar"))
        except OSError:
            core_jars = []
        for jar_path in core_jars:
            owner_id = _core_jar_owner(jar_path.name)
            jar_relative = _relative(installation_path, jar_path)
            _index_and_accumulate(jar_path, jar_relative, owner_id, result, prefix_owners, class_owners, entries, cache, updated_cache_entries)

    if use_cache and updated_cache_entries:
        merged_cache = {**cache, **updated_cache_entries}
        jar_index_cache.save_cache(cache_path, merged_cache)

    shared_prefixes = {prefix: sorted(owners) for prefix, owners in prefix_owners.items() if len(owners) > 1}

    for entry in entries:
        shared = [prefix for prefix in entry["package_prefixes"] if prefix in shared_prefixes]
        if shared and entry["confidence"] == "EXACT":
            owners: set[str] = set()
            for prefix in shared:
                owners.update(shared_prefixes[prefix])
            entry["confidence"] = "AMBIGUOUS"
            entry["owner_candidates"] = sorted(owners)

    result.jar_ownership = entries
    result.shared_package_prefixes = shared_prefixes
    result.package_prefix_index = {prefix: sorted(owners) for prefix, owners in prefix_owners.items()}
    result.class_index = {class_name: sorted(owners) for class_name, owners in class_owners.items()}
