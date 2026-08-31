import unittest
from unittest.mock import patch

from spw.gpu_detector import detect_gpus


class GpuDetectorTests(unittest.TestCase):
    def test_unavailable_on_non_windows(self) -> None:
        with patch("spw.gpu_detector.platform.system", return_value="Linux"):
            result = detect_gpus()
        self.assertEqual(result["source"], "UNAVAILABLE")
        self.assertEqual(result["gpus"], [])

    def test_unavailable_when_execution_disallowed(self) -> None:
        with patch("spw.gpu_detector.platform.system", return_value="Windows"):
            result = detect_gpus(allow_execute=False)
        self.assertEqual(result["source"], "UNAVAILABLE")

    def test_parses_single_gpu_object(self) -> None:
        with patch("spw.gpu_detector.platform.system", return_value="Windows"), patch("spw.gpu_detector.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = '{"Name":"NVIDIA GeForce RTX 5070 Laptop GPU","DriverVersion":"32.0.16.1088"}'
            result = detect_gpus()
        self.assertEqual(result["source"], "wmi")
        self.assertEqual(result["gpus"], [{"name": "NVIDIA GeForce RTX 5070 Laptop GPU", "driver_version": "32.0.16.1088"}])

    def test_parses_multiple_gpus_list(self) -> None:
        with patch("spw.gpu_detector.platform.system", return_value="Windows"), patch("spw.gpu_detector.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = '[{"Name":"Intel(R) Graphics","DriverVersion":"32.0.101.8991"},{"Name":"NVIDIA GeForce RTX 5070 Laptop GPU","DriverVersion":"32.0.16.1088"}]'
            result = detect_gpus()
        self.assertEqual(len(result["gpus"]), 2)
        self.assertEqual(result["source"], "wmi")

    def test_unavailable_on_subprocess_failure(self) -> None:
        with patch("spw.gpu_detector.platform.system", return_value="Windows"), patch("spw.gpu_detector.subprocess.run", side_effect=OSError("no powershell")):
            result = detect_gpus()
        self.assertEqual(result["source"], "UNAVAILABLE")

    def test_unavailable_on_unparseable_output(self) -> None:
        with patch("spw.gpu_detector.platform.system", return_value="Windows"), patch("spw.gpu_detector.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "not json"
            result = detect_gpus()
        self.assertEqual(result["source"], "UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
