import unittest

from spw.scenario_recorder import record_scenario_from_capture_descriptor


class ScenarioRecorderTests(unittest.TestCase):
    def test_records_a_valid_manifest_from_a_launch_descriptor(self) -> None:
        descriptor = {
            "capture_type": "launch",
            "target": "C:/jdk/bin/java.exe",
            "level": "STANDARD",
            "settings": {"duration_seconds": 30, "output_file": "out/profile.jfr", "launch_args": ["-jar", "starfarer.jar"]},
        }
        manifest = record_scenario_from_capture_descriptor(descriptor, name="my-scenario")
        self.assertEqual(manifest["name"], "my-scenario")
        self.assertEqual(manifest["java_executable"], "C:/jdk/bin/java.exe")
        self.assertEqual(manifest["launch_args"], ["-jar", "starfarer.jar"])
        self.assertEqual(manifest["duration_seconds"], 30)
        self.assertEqual(manifest["level"], "STANDARD")
        self.assertIn("limitations", manifest)

    def test_custom_description_is_used_when_given(self) -> None:
        descriptor = {"capture_type": "launch", "target": "java", "settings": {"duration_seconds": 5, "launch_args": ["-version"]}}
        manifest = record_scenario_from_capture_descriptor(descriptor, name="x", description="custom text")
        self.assertEqual(manifest["description"], "custom text")

    def test_attach_mode_descriptor_is_rejected(self) -> None:
        descriptor = {"capture_type": "attach", "target": "pid:123", "settings": {"duration_seconds": 30}}
        with self.assertRaises(ValueError):
            record_scenario_from_capture_descriptor(descriptor, name="x")

    def test_descriptor_without_launch_args_is_rejected(self) -> None:
        descriptor = {"capture_type": "launch", "target": "java", "settings": {"duration_seconds": 30, "launch_args": []}}
        with self.assertRaises(ValueError):
            record_scenario_from_capture_descriptor(descriptor, name="x")


if __name__ == "__main__":
    unittest.main()
