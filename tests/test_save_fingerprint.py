import tempfile
import unittest
from pathlib import Path

from spw.save_fingerprint import find_saves, parse_save_descriptor

# Trimmed from a real descriptor.xml (see session notes: a genuine
# Starsector save, 111 mods enabled), not a hand-guessed shape.
REAL_DESCRIPTOR_EXCERPT = """<?xml version="1.0" ?>
<SaveGameData z="1">
<portraitName>graphics/portraits/vic_dude2.jpg</portraitName>
<characterName>Warren Koyamatsu</characterName>
<saveFileVersion>0.6</saveFileVersion>
<gameVersion>0.98a-RC8</gameVersion>
<characterLevel>3</characterLevel>
<compressed>false</compressed>
<isIronMode>false</isIronMode>
<difficulty>normal</difficulty>
<gameDate z="2">
<secondsPerDay>10.0</secondsPerDay>
<timestamp>-55651368950000</timestamp>
</gameDate>
<saveDate z="3">2026-08-10 01:43:55.496 UTC</saveDate>
<slotCreationTimestamp>1786326235496</slotCreationTimestamp>
<allModsEverEnabled z="4">
<EnabledModData z="5">
<spec z="6">
<id>example_mod</id>
</spec>
</EnabledModData>
<EnabledModData z="7">
<spec z="8">
<id>another_mod</id>
</spec>
</EnabledModData>
</allModsEverEnabled>
<enabledMods z="9">
<EnabledModData z="10">
<spec ref="6"></spec>
</EnabledModData>
</enabledMods>
</SaveGameData>
"""


class SaveFingerprintTests(unittest.TestCase):
    def test_find_saves_requires_descriptor_xml(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            saves = root / "saves"
            good = saves / "save_a"
            good.mkdir(parents=True)
            (good / "descriptor.xml").write_text(REAL_DESCRIPTOR_EXCERPT, encoding="utf-8")
            bad = saves / "not_a_save"
            bad.mkdir()

            found = find_saves(root)
            self.assertEqual([p.name for p in found], ["save_a"])

    def test_find_saves_empty_without_saves_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(find_saves(Path(directory)), [])

    def test_parse_real_descriptor_excerpt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            save_dir = Path(directory)
            (save_dir / "descriptor.xml").write_text(REAL_DESCRIPTOR_EXCERPT, encoding="utf-8")
            result = parse_save_descriptor(save_dir)

        self.assertEqual(result["parse_status"], "PARSED")
        self.assertEqual(result["character_name"], "Warren Koyamatsu")
        self.assertEqual(result["character_level"], "3")
        self.assertEqual(result["game_version"], "0.98a-RC8")
        self.assertEqual(result["difficulty"], "normal")
        self.assertEqual(result["mods_ever_enabled_count"], 2)
        self.assertEqual(result["enabled_mod_count"], 1)

    def test_unreadable_descriptor_reports_status_not_exception(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            save_dir = Path(directory)
            (save_dir / "descriptor.xml").write_text("not xml at all <", encoding="utf-8")
            result = parse_save_descriptor(save_dir)
        self.assertEqual(result["parse_status"], "UNREADABLE")


if __name__ == "__main__":
    unittest.main()
