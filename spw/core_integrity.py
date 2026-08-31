from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .models import FingerprintResult

DEFAULT_CANDIDATE_FILES = (
    "starsector.exe",
    "starsector.bat",
    "starsector.sh",
    "vmparams",
    "vmparams.txt",
    "starsector-core/starfarer.api.jar",
    "starsector-core/starfarer_obf.jar",
    "starsector-core/starfarer.jar",
)

_STATUS_RANK = {
    "MATCHES_BASELINE": 0,
    "KNOWN_MODIFICATION": 1,
    "BASELINE_UNAVAILABLE": 1,
    "UNKNOWN_DIFFERENCE": 2,
    "UNREADABLE": 3,
}


def hash_file(path: Path) -> str | None:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def load_baseline_catalog(path: Path) -> dict[str, Any]:
    """Load a versioned baseline catalog: `{"<starsector-build>": {"<relative-path>": [{"sha256": ..., "classification": "MATCHES_BASELINE"|"KNOWN_MODIFICATION"}, ...]}}`."""

    return json.loads(path.read_text(encoding="utf-8"))


def classify_core_integrity(
    installation_path: Path,
    result: FingerprintResult,
    candidate_files: tuple[str, ...] = DEFAULT_CANDIDATE_FILES,
    baseline_catalog: dict[str, Any] | None = None,
    starsector_build: str | None = None,
) -> None:
    """Hash the selected core/launcher files and compare against an explicit, build-matched baseline catalog.

    Hashing alone never establishes modification: without a baseline
    catalog entry for the exact detected build, every hashed file is
    reported `BASELINE_UNAVAILABLE` rather than guessed.
    """

    file_hashes: dict[str, str] = {}
    file_status: dict[str, str] = {}

    for relative in candidate_files:
        full_path = installation_path / relative
        if not full_path.is_file():
            continue
        digest = hash_file(full_path)
        if digest is None:
            file_status[relative] = "UNREADABLE"
            result.add(
                id="unreadable-core-file",
                category="core-integrity",
                severity="medium",
                confidence="DETERMINISTIC",
                explanation="A selected core/launcher file exists but could not be hashed.",
                file=relative,
            )
            continue
        file_hashes[relative] = digest

    catalog_entries = None
    if baseline_catalog is not None and starsector_build is not None:
        catalog_entries = baseline_catalog.get(starsector_build)

    if catalog_entries is None:
        for relative in file_hashes:
            file_status[relative] = "BASELINE_UNAVAILABLE"
        if baseline_catalog is None:
            result.add(
                id="baseline-catalog-unavailable",
                category="core-integrity",
                severity="low",
                confidence="DETERMINISTIC",
                explanation="No baseline catalog was supplied; core-integrity status cannot be more specific than BASELINE_UNAVAILABLE.",
            )
        elif starsector_build is None:
            result.add(
                id="baseline-catalog-build-not-specified",
                category="core-integrity",
                severity="low",
                confidence="DETERMINISTIC",
                explanation="A baseline catalog was supplied, but no Starsector build was given to select an entry from it; pass --starsector-build to use it.",
            )
        else:
            # The catalog exists and a build was named, but that build has
            # no entry in it -- unlike the two cases above, this points at
            # a catalog that is out of date relative to the installation,
            # not simply absent, and the fix is different (get/build a
            # newer catalog) so it should not read the same as "no catalog
            # at all".
            result.add(
                id="baseline-catalog-stale",
                category="core-integrity",
                severity="medium",
                confidence="DETERMINISTIC",
                explanation=f"The supplied baseline catalog has no entry for Starsector build '{starsector_build}'; a newer catalog covering this build is expected but was not found.",
                evidence=[starsector_build],
            )
    else:
        for relative, digest in file_hashes.items():
            variants = catalog_entries.get(relative)
            if not variants:
                file_status[relative] = "UNKNOWN_DIFFERENCE"
                continue
            match = next((variant for variant in variants if variant.get("sha256") == digest), None)
            if match is None:
                file_status[relative] = "UNKNOWN_DIFFERENCE"
                result.add(
                    id="unknown-core-difference",
                    category="core-integrity",
                    severity="high",
                    confidence="DETERMINISTIC",
                    explanation="Hash does not match any known variant in the baseline catalog for the detected build.",
                    file=relative,
                    evidence=[digest],
                )
            else:
                file_status[relative] = str(match.get("classification", "UNKNOWN_DIFFERENCE"))

    if file_status:
        overall = max(file_status.values(), key=lambda status: _STATUS_RANK.get(status, 2))
    else:
        overall = "BASELINE_UNAVAILABLE"

    result.core_integrity = {
        "candidate_files": list(candidate_files),
        "file_hashes": file_hashes,
        "file_status": file_status,
        "baseline_catalog_matched_build": starsector_build if catalog_entries is not None else None,
        "status": overall,
    }
