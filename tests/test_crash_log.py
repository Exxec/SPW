import tempfile
import unittest
from pathlib import Path

from spw.crash_log import find_crash_logs, parse_crash_log

# Trimmed from a real hs_err_pid*.log generated on this machine via
# `-Xmx32m -XX:+CrashOnOutOfMemoryError` (see session notes) -- not a
# hand-guessed format.
REAL_HS_ERR_EXCERPT = """#
# A fatal error has been detected by the Java Runtime Environment:
#
#  Internal Error (debug.cpp:289), pid=36016, tid=43752
#  fatal error: OutOfMemory encountered: Java heap space
#
# JRE version: OpenJDK Runtime Environment Temurin-25.0.4.1+1 (25.0.4.1+1) (build 25.0.4.1+1-LTS)
# Java VM: OpenJDK 64-Bit Server VM Temurin-25.0.4.1+1 (25.0.4.1+1-LTS, mixed mode, sharing, tiered, compressed oops, compressed class ptrs, g1 gc, windows-amd64)
# No core dump will be written. Minidumps are not enabled by default on client versions of Windows
#

---------------  S U M M A R Y ------------

Command Line: -Xmx32m -XX:+CrashOnOutOfMemoryError OomCrash

Host: Intel(R) Core(TM) Ultra 9 275HX, 24 cores, 31G,  Windows 11 , 64 bit Build 26100 (10.0.26100.9168)
Time: Mon Aug 31 13:42:39 2026 Central Daylight Time elapsed time: 0.040345 seconds (0d 0h 0m 0s)

---------------  T H R E A D  ---------------

Current thread (0x0000027e953a3250):  JavaThread "main"             [_thread_in_vm, id=43752, stack(0x000000e4e2f00000,0x000000e4e3000000) (1024K)]

Stack: [0x000000e4e2f00000,0x000000e4e3000000]
Native frames: (J=compiled Java code, j=interpreted, Vv=VM code, C=native code)
V  [jvm.dll+0x7aba79]  (no source info available)

The last pc belongs to newarray (printed below).
Java frames: (J=compiled Java code, j=interpreted, Vv=VM code)
j  OomCrash.main([Ljava/lang/String;)V+11
v  ~StubRoutines::call_stub 0x0000027ea78212ed
Lock stack of current Java thread (top to bottom):
"""


class CrashLogTests(unittest.TestCase):
    def test_find_crash_logs_is_non_recursive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "hs_err_pid123.log").write_text("x", encoding="utf-8")
            nested = root / "mods" / "some_mod"
            nested.mkdir(parents=True)
            (nested / "hs_err_pid456.log").write_text("x", encoding="utf-8")

            found = find_crash_logs(root)
            self.assertEqual([p.name for p in found], ["hs_err_pid123.log"])

    def test_find_crash_logs_empty_when_none_present(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(find_crash_logs(Path(directory)), [])

    def test_parse_real_crash_log_excerpt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hs_err_pid36016.log"
            path.write_text(REAL_HS_ERR_EXCERPT, encoding="utf-8")
            result = parse_crash_log(path)

        self.assertEqual(result["parse_status"], "PARSED")
        self.assertEqual(result["pid"], 36016)
        self.assertEqual(result["tid"], 43752)
        self.assertIn("fatal error: OutOfMemory encountered: Java heap space", result["problem_summary"])
        self.assertTrue(result["jre_version"].startswith("OpenJDK Runtime Environment"))
        self.assertTrue(result["java_vm"].startswith("OpenJDK 64-Bit Server VM"))
        self.assertEqual(result["command_line"], "-Xmx32m -XX:+CrashOnOutOfMemoryError OomCrash")
        self.assertAlmostEqual(result["elapsed_seconds"], 0.040345)
        self.assertEqual(result["java_frames"][0], "j  OomCrash.main([Ljava/lang/String;)V+11")

    def test_unreadable_file_reports_status_not_exception(self) -> None:
        result = parse_crash_log(Path("does-not-exist.log"))
        self.assertEqual(result["parse_status"], "UNREADABLE")


if __name__ == "__main__":
    unittest.main()
