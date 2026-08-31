import tempfile
import unittest
from pathlib import Path

from spw import jar_index_cache


class JarIndexCacheTests(unittest.TestCase):
    def test_missing_cache_file_loads_empty(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "does-not-exist.json"
            self.assertEqual(jar_index_cache.load_cache(path), {})

    def test_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cache.json"
            entries = {"abc123": {"prefixes": ["a/b"], "classes": ["a/b/C"]}}
            jar_index_cache.save_cache(path, entries)
            self.assertEqual(jar_index_cache.load_cache(path), entries)

    def test_wrong_schema_version_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cache.json"
            path.write_text('{"schema_version": 999, "entries": {"x": {}}}', encoding="utf-8")
            self.assertEqual(jar_index_cache.load_cache(path), {})

    def test_corrupt_file_is_ignored_not_raised(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cache.json"
            path.write_text("not json", encoding="utf-8")
            self.assertEqual(jar_index_cache.load_cache(path), {})

    def test_hash_file_is_stable_for_identical_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path_a = Path(directory) / "a.jar"
            path_b = Path(directory) / "b.jar"
            path_a.write_bytes(b"same content")
            path_b.write_bytes(b"same content")
            self.assertEqual(jar_index_cache.hash_file(path_a), jar_index_cache.hash_file(path_b))

    def test_hash_file_returns_none_for_missing_file(self) -> None:
        self.assertIsNone(jar_index_cache.hash_file(Path("does-not-exist.jar")))


if __name__ == "__main__":
    unittest.main()
