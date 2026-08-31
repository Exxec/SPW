import hashlib
import json
import tempfile
import unittest
import unittest.mock
import zipfile
from pathlib import Path

from spw.capture import build_attach_start_command, build_attach_stop_command, build_launch_command
from spw.cli import main as cli_main
from spw.core_integrity import classify_core_integrity, hash_file
from spw.java_detector import detect_java
from spw.jar_ownership import CORE_OWNER_ID, FAST_RENDERING_OWNER_ID, build_jar_ownership
from spw.mod_inventory import build_inventory
from spw.models import FingerprintResult
from spw.report import write_artifacts
from spw.vmparams import parse_configured_jvm_arguments


def _write_jar(path: Path, class_names: list[str]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name in class_names:
            archive.writestr(name, b"\xca\xfe\xba\xbe\x00\x00\x00\x34")


class JavaDetectorTests(unittest.TestCase):
    def test_detects_from_release_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "bin").mkdir()
            executable = root / "bin" / "java.exe"
            executable.write_text("", encoding="utf-8")
            (root / "release").write_text(
                'IMPLEMENTOR="Eclipse Adoptium"\nJAVA_VERSION="17.0.9"\nOS_ARCH="x86_64"\n',
                encoding="utf-8",
            )
            info = detect_java(executable, allow_execute=False)
            self.assertEqual(info["implementor"], "Eclipse Adoptium")
            self.assertEqual(info["java_version"], "17.0.9")
            self.assertEqual(info["source"], "release-file")

    def test_unavailable_when_executable_missing(self) -> None:
        info = detect_java(Path("does-not-exist/java.exe"), allow_execute=False)
        self.assertEqual(info["source"], "UNAVAILABLE")


class VmparamsTests(unittest.TestCase):
    def test_keeps_only_flag_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "vmparams"
            path.write_text('java.exe -server -Xms2g -Xmx2g -DlaunchDirect=true "%~dp0"', encoding="utf-8")
            args = parse_configured_jvm_arguments(path)
            self.assertEqual(args, ["-server", "-Xms2g", "-Xmx2g", "-DlaunchDirect=true"])


class ModInventoryAndOwnershipTests(unittest.TestCase):
    def _installation(self, root: Path) -> None:
        mods = root / "mods"
        mods.mkdir()
        (mods / "enabled_mods.json").write_text(json.dumps({"enabledMods": ["alpha_mod", "missing_mod"]}), encoding="utf-8")

        alpha = mods / "alpha_dir"
        alpha.mkdir()
        (alpha / "mod_info.json").write_text(json.dumps({"id": "alpha_mod", "dependencies": ["beta_mod"]}), encoding="utf-8")
        _write_jar(alpha / "shared_lib.jar", ["org/shared/Thing.class", "alpha/Own.class"])

        beta = mods / "beta_dir"
        beta.mkdir()
        (beta / "mod_info.json").write_text(json.dumps({"id": "beta_mod"}), encoding="utf-8")
        _write_jar(beta / "shared_lib.jar", ["org/shared/Thing.class", "beta/Own.class"])

        broken = mods / "broken_dir"
        broken.mkdir()
        (broken / "mod_info.json").write_text("{not json", encoding="utf-8")

    def test_inventory_and_ownership(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._installation(root)
            result = FingerprintResult(installation_path=root)
            build_inventory(root, result)

            local_ids = {mod["local_id"] for mod in result.mods}
            self.assertEqual(local_ids, {"alpha_mod", "beta_mod", "broken_dir"})
            self.assertTrue(any(f.id == "enabled-mod-not-found" for f in result.findings))
            self.assertTrue(any(f.id == "invalid-mod-info" for f in result.findings))
            alpha_entry = next(mod for mod in result.mods if mod["local_id"] == "alpha_mod")
            self.assertTrue(alpha_entry["enabled"])

            build_jar_ownership(root, result, use_cache=False)
            self.assertIn("org/shared", result.shared_package_prefixes)
            self.assertEqual(sorted(result.shared_package_prefixes["org/shared"]), ["alpha_mod", "beta_mod"])
            ambiguous = [e for e in result.jar_ownership if e["confidence"] == "AMBIGUOUS"]
            self.assertEqual(len(ambiguous), 2)

    def test_disabled_via_rename_is_never_enabled_even_if_id_is_listed(self) -> None:
        # Mirrors a real installation: an old copy disabled via renaming
        # mod_info.json to mod_info.json.disabled sits next to a newer
        # copy sharing the same declared id, which *is* in enabled_mods.json.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mods = root / "mods"
            mods.mkdir()
            (mods / "enabled_mods.json").write_text(json.dumps({"enabledMods": ["shared_id"]}), encoding="utf-8")

            old_copy = mods / "Thing-old"
            old_copy.mkdir()
            (old_copy / "mod_info.json.disabled").write_text(json.dumps({"id": "shared_id"}), encoding="utf-8")

            new_copy = mods / "Thing-new"
            new_copy.mkdir()
            (new_copy / "mod_info.json").write_text(json.dumps({"id": "shared_id"}), encoding="utf-8")

            result = FingerprintResult(installation_path=root)
            build_inventory(root, result)

            by_root = {mod["relative_root"]: mod for mod in result.mods}
            self.assertFalse(by_root["mods/Thing-old"]["enabled"])
            self.assertEqual(by_root["mods/Thing-old"]["metadata_parse_status"], "DISABLED")
            self.assertTrue(by_root["mods/Thing-new"]["enabled"])
            self.assertTrue(any(f.id == "mod-disabled-via-rename" for f in result.findings))
            self.assertTrue(any(f.id == "duplicate-mod-id" for f in result.findings))

    def test_tick_marker_mod_not_installed_is_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._installation(root)
            result = FingerprintResult(installation_path=root)
            build_inventory(root, result)
            self.assertTrue(any(f.id == "tick-marker-mod-not-installed" for f in result.findings))

    def test_tick_marker_mod_installed_but_not_enabled_is_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._installation(root)
            tick_marker = root / "mods" / "spw-tick-marker"
            tick_marker.mkdir()
            (tick_marker / "mod_info.json").write_text(json.dumps({"id": "spw_tick_marker"}), encoding="utf-8")

            result = FingerprintResult(installation_path=root)
            build_inventory(root, result)
            self.assertFalse(any(f.id == "tick-marker-mod-not-installed" for f in result.findings))
            self.assertTrue(any(f.id == "tick-marker-mod-not-enabled" for f in result.findings))

    def test_tick_marker_mod_installed_and_enabled_has_no_finding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mods = root / "mods"
            mods.mkdir()
            (mods / "enabled_mods.json").write_text(json.dumps({"enabledMods": ["spw_tick_marker"]}), encoding="utf-8")
            tick_marker = mods / "spw-tick-marker"
            tick_marker.mkdir()
            (tick_marker / "mod_info.json").write_text(json.dumps({"id": "spw_tick_marker"}), encoding="utf-8")

            result = FingerprintResult(installation_path=root)
            build_inventory(root, result)
            self.assertFalse(any(f.id in ("tick-marker-mod-not-installed", "tick-marker-mod-not-enabled") for f in result.findings))

    def test_starsector_core_jars_are_indexed_under_reserved_owner_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._installation(root)
            core = root / "starsector-core"
            core.mkdir()
            _write_jar(core / "starfarer_obf.jar", ["com/fs/starfarer/Engine.class"])
            _write_jar(core / "fr.jar", ["com/fastrendering/Bridge.class"])
            # A base-game-bundled library sharing a package prefix with a
            # mod's own bundled copy must show up as ambiguous, not silently
            # attributed to just one side.
            _write_jar(core / "log4j-1.2.9.jar", ["org/shared/Logger.class"])

            result = FingerprintResult(installation_path=root)
            build_inventory(root, result)
            build_jar_ownership(root, result, use_cache=False)

            self.assertIn("com/fs/starfarer/Engine", result.class_index)
            self.assertEqual(result.class_index["com/fs/starfarer/Engine"], [CORE_OWNER_ID])
            self.assertEqual(result.class_index["com/fastrendering/Bridge"], [FAST_RENDERING_OWNER_ID])
            self.assertIn("org/shared", result.shared_package_prefixes)
            self.assertIn(CORE_OWNER_ID, result.shared_package_prefixes["org/shared"])
            self.assertIn("alpha_mod", result.shared_package_prefixes["org/shared"])

    def test_repeat_scan_reuses_cached_jar_index_without_reopening_the_jar(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._installation(root)
            cache_dir = Path(directory) / "cache"
            cache_path = cache_dir / "jar-index-cache.json"

            result_a = FingerprintResult(installation_path=root)
            build_inventory(root, result_a)
            build_jar_ownership(root, result_a, use_cache=True, cache_path=cache_path)
            self.assertTrue(cache_path.is_file())
            first_run_class_index = result_a.class_index

            result_b = FingerprintResult(installation_path=root)
            build_inventory(root, result_b)
            with unittest.mock.patch("spw.jar_ownership.zipfile.ZipFile") as mock_zip:
                build_jar_ownership(root, result_b, use_cache=True, cache_path=cache_path)
            # Every jar's content hash matched the cache from the first
            # run, so the second run must never have opened a jar's zip
            # contents at all.
            mock_zip.assert_not_called()
            self.assertEqual(result_b.class_index, first_run_class_index)


class CoreIntegrityTests(unittest.TestCase):
    def test_baseline_unavailable_without_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "vmparams").write_text("java.exe -Xms1g", encoding="utf-8")
            result = FingerprintResult(installation_path=root)
            classify_core_integrity(root, result, candidate_files=("vmparams",))
            self.assertEqual(result.core_integrity["status"], "BASELINE_UNAVAILABLE")

    def test_matches_and_unknown_difference(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            core_file = root / "vmparams"
            core_file.write_text("java.exe -Xms1g", encoding="utf-8")
            digest = hash_file(core_file)
            self.assertIsNotNone(digest)
            catalog = {"0.98a": {"vmparams": [{"sha256": digest, "classification": "MATCHES_BASELINE"}]}}
            result = FingerprintResult(installation_path=root)
            classify_core_integrity(root, result, candidate_files=("vmparams",), baseline_catalog=catalog, starsector_build="0.98a")
            self.assertEqual(result.core_integrity["status"], "MATCHES_BASELINE")

            core_file.write_text("java.exe -Xms1g -modified", encoding="utf-8")
            result2 = FingerprintResult(installation_path=root)
            classify_core_integrity(root, result2, candidate_files=("vmparams",), baseline_catalog=catalog, starsector_build="0.98a")
            self.assertEqual(result2.core_integrity["status"], "UNKNOWN_DIFFERENCE")
            self.assertTrue(any(f.id == "unknown-core-difference" for f in result2.findings))

    def test_stale_catalog_distinguished_from_no_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "vmparams").write_text("java.exe -Xms1g", encoding="utf-8")
            catalog = {"0.97a": {}}

            result = FingerprintResult(installation_path=root)
            classify_core_integrity(root, result, candidate_files=("vmparams",), baseline_catalog=catalog, starsector_build="0.98a")
            self.assertTrue(any(f.id == "baseline-catalog-stale" for f in result.findings))
            self.assertFalse(any(f.id == "baseline-catalog-unavailable" for f in result.findings))

    def test_catalog_without_build_specified(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "vmparams").write_text("java.exe -Xms1g", encoding="utf-8")
            catalog = {"0.98a": {}}

            result = FingerprintResult(installation_path=root)
            classify_core_integrity(root, result, candidate_files=("vmparams",), baseline_catalog=catalog)
            self.assertTrue(any(f.id == "baseline-catalog-build-not-specified" for f in result.findings))


class CaptureCommandBuilderTests(unittest.TestCase):
    def test_build_attach_commands(self) -> None:
        jcmd = Path("C:/jdk/bin/jcmd.exe")
        start = build_attach_start_command(jcmd, 1234, Path("out/profile.jfr"), 60)
        self.assertIn("JFR.start", start)
        self.assertTrue(any(part.startswith("duration=60s") for part in start))
        stop = build_attach_stop_command(jcmd, 1234)
        self.assertIn("JFR.stop", stop)

    def test_build_launch_command_inserts_flight_recording_flag(self) -> None:
        java = Path("C:/jdk/bin/java.exe")
        command = build_launch_command(java, ["-jar", "starfarer.jar"], Path("out/profile.jfr"), 30)
        self.assertEqual(command[0], str(java))
        self.assertIn("-XX:StartFlightRecording", command[1])
        self.assertEqual(command[-2:], ["-jar", "starfarer.jar"])


class FindingsCategorizationTests(unittest.TestCase):
    def test_findings_are_filtered_into_the_file_they_describe(self) -> None:
        result = FingerprintResult(installation_path=Path("."))
        result.add(id="a", category="core-integrity", severity="high", confidence="DETERMINISTIC", explanation="x")
        result.add(id="b", category="mod-inventory", severity="high", confidence="DETERMINISTIC", explanation="y")
        result.add(id="c", category="jar-ownership", severity="high", confidence="DETERMINISTIC", explanation="z")

        environment_ids = {f["id"] for f in result.environment_dict()["findings"]}
        core_ids = {f["id"] for f in result.core_integrity_dict()["findings"]}
        mod_ids = {f["id"] for f in result.mod_ownership_dict()["findings"]}

        self.assertEqual(environment_ids, {"a", "b", "c"})
        self.assertEqual(core_ids, {"a"})
        self.assertEqual(mod_ids, {"b", "c"})


class ReportWriteTests(unittest.TestCase):
    def test_refuses_output_inside_installation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = FingerprintResult(installation_path=root)
            with self.assertRaises(ValueError):
                write_artifacts(result, root / "artifacts")

    def test_writes_expected_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "install"
            root.mkdir()
            output = Path(directory) / "artifacts"
            result = FingerprintResult(installation_path=root)
            paths = write_artifacts(result, output)
            for path in paths.values():
                self.assertTrue(path.is_file())


class CliInventoryEndToEndTests(unittest.TestCase):
    def test_inventory_command_writes_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "install"
            mods = root / "mods"
            mods.mkdir(parents=True)
            (mods / "enabled_mods.json").write_text(json.dumps({"enabledMods": ["only_mod"]}), encoding="utf-8")
            only = mods / "only_dir"
            only.mkdir()
            (only / "mod_info.json").write_text(json.dumps({"id": "only_mod"}), encoding="utf-8")
            output = Path(directory) / "artifacts"

            exit_code = cli_main(["inventory", str(root), "--output", str(output)])
            self.assertEqual(exit_code, 0)
            self.assertTrue((output / "environment.json").is_file())
            self.assertTrue((output / "mod-ownership.json").is_file())
            self.assertTrue((output / "core-integrity.json").is_file())
            self.assertTrue((output / "PERFORMANCE_REPORT.md").is_file())

            ownership = json.loads((output / "mod-ownership.json").read_text(encoding="utf-8"))
            self.assertEqual(len(ownership["mods"]), 1)


if __name__ == "__main__":
    unittest.main()
