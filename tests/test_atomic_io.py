import ast
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from spw import atomic_io, jar_index_cache
from spw.analysis import write_analysis_artifacts
from spw.interchange import write_json

PACKAGE = Path(__file__).resolve().parent.parent / "spw"
EXEMPT = {"atomic_io.py"}


class AtomicWriteTests(unittest.TestCase):
    def test_success_replaces_file_whole_and_leaves_no_temporary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.md"
            path.write_text("old old old old", encoding="utf-8")
            atomic_io.atomic_write_text(path, "new")
            self.assertEqual(path.read_text(encoding="utf-8"), "new")
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_json_helper_matches_standard_layout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "x.json"
            atomic_io.atomic_write_json(path, {"b": 1, "a": [2]})
            self.assertEqual(path.read_text(encoding="utf-8"), json.dumps({"a": [2], "b": 1}, indent=2))

    def test_failed_replace_preserves_previous_artifact_and_propagates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_bytes(b"previous")
            with patch.object(atomic_io.os, "replace", side_effect=OSError("blocked")):
                with self.assertRaises(OSError):
                    atomic_io.atomic_write_text(path, "new")
            self.assertEqual(path.read_bytes(), b"previous")
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_failed_write_preserves_previous_artifact_and_cleans_temporary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_bytes(b"previous")
            with patch.object(atomic_io.os, "fsync", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    atomic_io.atomic_write_text(path, "new")
            self.assertEqual(path.read_bytes(), b"previous")
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_interchange_write_json_failure_propagates_and_keeps_old_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "performance-report.json"
            write_json(path, {"v": 1})
            original = path.read_bytes()
            with patch.object(atomic_io.os, "replace", side_effect=OSError("blocked")):
                with self.assertRaises(OSError):
                    write_json(path, {"v": 2})
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_analysis_artifact_failure_propagates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(atomic_io.os, "replace", side_effect=OSError("blocked")):
                with self.assertRaises(OSError):
                    write_analysis_artifacts({"cpu_thread": {}}, Path(directory))
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_cache_stays_best_effort_when_helper_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cache.json"
            with patch.object(atomic_io.os, "replace", side_effect=OSError("blocked")):
                jar_index_cache.save_cache(path, {})
            self.assertEqual(list(Path(directory).iterdir()), [])


class NoBareReportWritesRatchet(unittest.TestCase):
    def test_package_has_no_bare_text_writes(self) -> None:
        """Report artifacts go through spw.atomic_io; a bare write_text/write_bytes/open-for-write fails here."""
        offenders = []
        for source in sorted(PACKAGE.glob("*.py")):
            if source.name in EXEMPT:
                continue
            for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute | ast.Name):
                    continue
                name = node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
                bare = name in {"write_text", "write_bytes"}
                if name == "open" and len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                    bare = bare or any(flag in str(node.args[1].value) for flag in "wax+")
                if name == "open" and any(k.arg == "mode" and isinstance(k.value, ast.Constant)
                                          and any(flag in str(k.value.value) for flag in "wax+") for k in node.keywords):
                    bare = True
                if bare:
                    offenders.append(f"{source.name}:{node.lineno}")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
