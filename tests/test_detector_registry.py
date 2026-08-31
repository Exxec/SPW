import tempfile
import textwrap
import unittest
from pathlib import Path

from spw.cli import main as cli_main
from spw.runtime_capability import (
    DetectorContext,
    detect_runtime_capabilities,
    register_detector,
    reset_detector_registry,
    unregister_detector,
)


class DetectorRegistryTests(unittest.TestCase):
    def tearDown(self) -> None:
        # The registry is module-level mutable state; never let one test's
        # registration leak into another.
        reset_detector_registry()

    def test_registered_detector_result_is_included(self) -> None:
        def custom_detector(context: DetectorContext) -> list[dict]:
            return [{"id": "CustomThing", "version": None, "state": "ENABLED", "evidence": ["custom"], "parsed_configuration": {}, "unsupported_assumptions": []}]

        register_detector(custom_detector)
        with tempfile.TemporaryDirectory() as directory:
            capabilities = detect_runtime_capabilities(Path(directory), {"source": "UNAVAILABLE"}, {})
        self.assertTrue(any(c["id"] == "CustomThing" for c in capabilities))

    def test_unregister_removes_a_detector(self) -> None:
        def custom_detector(context: DetectorContext) -> list[dict]:
            return [{"id": "ShouldNotAppear", "version": None, "state": "ENABLED", "evidence": [], "parsed_configuration": {}, "unsupported_assumptions": []}]

        register_detector(custom_detector)
        unregister_detector(custom_detector)
        with tempfile.TemporaryDirectory() as directory:
            capabilities = detect_runtime_capabilities(Path(directory), {"source": "UNAVAILABLE"}, {})
        self.assertFalse(any(c["id"] == "ShouldNotAppear" for c in capabilities))

    def test_a_raising_detector_does_not_break_the_others(self) -> None:
        def broken_detector(context: DetectorContext) -> list[dict]:
            raise RuntimeError("boom")

        register_detector(broken_detector)
        with tempfile.TemporaryDirectory() as directory:
            capabilities = detect_runtime_capabilities(Path(directory), {"source": "UNAVAILABLE"}, {})
        # The built-in base-JDK detectors still ran.
        self.assertTrue(any(c["id"] == "VanillaJava17" for c in capabilities))
        broken = next(c for c in capabilities if c["id"] == "broken_detector")
        self.assertEqual(broken["state"], "UNKNOWN")
        self.assertIn("RuntimeError", broken["evidence"][0])


class ExtraDetectorsDirCliTests(unittest.TestCase):
    def tearDown(self) -> None:
        reset_detector_registry()

    def test_extra_detectors_dir_is_loaded_and_registered(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            install = root / "install"
            (install / "mods").mkdir(parents=True)
            (install / "mods" / "enabled_mods.json").write_text("{\"enabledMods\": []}", encoding="utf-8")

            detectors_dir = root / "detectors"
            detectors_dir.mkdir()
            (detectors_dir / "my_detector.py").write_text(
                textwrap.dedent(
                    """
                    def detect(context):
                        return [{"id": "MyThirdPartyThing", "version": None, "state": "ENABLED", "evidence": [], "parsed_configuration": {}, "unsupported_assumptions": []}]

                    DETECTORS = [detect]
                    """
                ),
                encoding="utf-8",
            )

            output = root / "artifacts"
            exit_code = cli_main(["inventory", str(install), "--output", str(output), "--extra-detectors-dir", str(detectors_dir)])
            self.assertEqual(exit_code, 0)

            import json

            capabilities = json.loads((output / "runtime-capabilities.json").read_text(encoding="utf-8"))["capabilities"]
            # DETECTORS = [detect] means `detect` is registered twice here
            # (once via DETECTORS, and the loader also falls back to
            # `detect` only when DETECTORS is absent) -- assert at least one.
            self.assertTrue(any(c["id"] == "MyThirdPartyThing" for c in capabilities))

    def test_missing_extra_detectors_dir_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            install = root / "install"
            install.mkdir()
            exit_code = cli_main(["inventory", str(install), "--output", str(root / "out"), "--extra-detectors-dir", str(root / "does-not-exist")])
        self.assertEqual(exit_code, 2)


if __name__ == "__main__":
    unittest.main()
