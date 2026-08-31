from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


def find_saves(installation_path: Path) -> list[Path]:
    """Find save directories (each holding a `descriptor.xml`) under `<installation>/saves/`."""

    saves_dir = installation_path / "saves"
    if not saves_dir.is_dir():
        return []
    try:
        return sorted(path for path in saves_dir.iterdir() if path.is_dir() and (path / "descriptor.xml").is_file())
    except OSError:
        return []


def parse_save_descriptor(save_dir: Path) -> dict[str, Any]:
    """Parse a save's `descriptor.xml` for a lightweight, safe complexity summary.

    Deliberately reads only `descriptor.xml` (tens to low hundreds of KB)
    and never the much larger `campaign.xml` alongside it (real saves
    observed at 50+ MB). Checked directly against a real save whether
    campaign.xml's market/fleet-related tags could safely yield colony or
    fleet counts: they cannot without deeper, unverified structural
    assumptions -- `<Market ` alone appeared 1,081 times in one save,
    mixing real markets with data references and spawn templates, so no
    attempt is made here to extract those counts. This reports only
    fields descriptor.xml states directly and unambiguously: mod counts
    (a real, cheap complexity signal in its own right) plus save/character
    metadata already used elsewhere in this project (game version,
    difficulty).
    """

    descriptor_path = save_dir / "descriptor.xml"
    try:
        tree = ET.parse(descriptor_path)
    except (OSError, ET.ParseError) as exc:
        return {"path": str(descriptor_path), "parse_status": "UNREADABLE", "detail": str(exc)}

    root = tree.getroot()

    def text(tag: str) -> str | None:
        element = root.find(tag)
        return element.text if element is not None else None

    enabled_mods = root.find("enabledMods")
    all_mods_ever_enabled = root.find("allModsEverEnabled")

    return {
        "path": str(descriptor_path),
        "parse_status": "PARSED",
        "character_name": text("characterName"),
        "character_level": text("characterLevel"),
        "save_file_version": text("saveFileVersion"),
        "game_version": text("gameVersion"),
        "difficulty": text("difficulty"),
        "is_iron_mode": text("isIronMode"),
        "save_date": text("saveDate"),
        "enabled_mod_count": len(enabled_mods) if enabled_mods is not None else None,
        "mods_ever_enabled_count": len(all_mods_ever_enabled) if all_mods_ever_enabled is not None else None,
    }
