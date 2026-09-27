from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from spw.interchange import mod_identity_export, performance_export
from spw.models import FingerprintResult


class InterchangeTests(unittest.TestCase):
    def test_identity_preserves_ambiguity_and_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ("one", "two"):
                folder = root / "mods" / name
                folder.mkdir(parents=True)
                (folder / "mod_info.json").write_text('{"id":"same"}', encoding="utf-8")
            result = FingerprintResult(installation_path=root)
            result.mods = [{"local_id": "same", "relative_root": f"mods/{name}",
                            "metadata_parse_status": "OK", "jar_paths": [], "enabled": True}
                           for name in ("one", "two")]
            report = mod_identity_export(result)
            self.assertTrue(all(mod["duplicate_id"] for mod in report["mods"]))
            self.assertEqual(hashlib.sha256(b'{"id":"same"}').hexdigest(), report["mods"][0]["metadata_sha256"])

    def test_failed_attribution_is_unknown_not_zero(self) -> None:
        inventory = {"mods": [{"id": "sample", "relative_root": "mods/sample",
                               "metadata_sha256": "abc", "enabled": True, "duplicate_id": False}]}
        analysis = {"attribution": {"samples_by_owner": {"sample": 0},
                                    "execution_samples_event_read": {"status": "READ_FAILED"}}}
        report = performance_export(analysis, inventory, {}, "STANDARD")
        self.assertIsNone(report["mods"][0]["attributed_samples"])
        self.assertIsNone(report["total_attributed_samples"])
        self.assertEqual("samples", report["unit"])
