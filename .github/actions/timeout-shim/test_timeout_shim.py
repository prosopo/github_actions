import subprocess
import time
import unittest
from pathlib import Path

SHIM = str(Path(__file__).with_name("timeout"))


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([SHIM, *args], capture_output=True, text=True, timeout=30)


class TimeoutShimTest(unittest.TestCase):
    def test_passes_through_exit_code_and_output(self) -> None:
        r = run("5", "sh", "-c", "echo hi; exit 3")
        self.assertEqual((r.returncode, r.stdout), (3, "hi\n"))

    def test_expiry_exits_124_promptly(self) -> None:
        start = time.monotonic()
        self.assertEqual(run("1", "sleep", "20").returncode, 124)
        self.assertLess(time.monotonic() - start, 5)

    def test_kills_command_that_ignores_term(self) -> None:
        start = time.monotonic()
        self.assertEqual(run("1", "sh", "-c", "trap '' TERM; sleep 20").returncode, 124)
        self.assertLess(time.monotonic() - start, 10)

    def test_minute_suffix_accepted(self) -> None:
        self.assertEqual(run("1m", "true").returncode, 0)

    def test_bad_duration_and_missing_command_fail(self) -> None:
        self.assertNotEqual(run("soon", "true").returncode, 0)
        self.assertNotEqual(run("5").returncode, 0)

    def test_missing_binary_exits_127(self) -> None:
        self.assertEqual(run("5", "no-such-command-xyz").returncode, 127)


if __name__ == "__main__":
    unittest.main()
