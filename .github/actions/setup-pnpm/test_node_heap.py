import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import node_heap
from node_heap import node_options, total_mib, total_mib_linux


class NodeOptionsTest(unittest.TestCase):
    def test_sets_share_of_ram(self) -> None:
        self.assertEqual(node_options("", 16384, 75), "--max-old-space-size=12288")

    def test_appends_to_existing(self) -> None:
        self.assertEqual(node_options("--enable-source-maps", 8000, 75), "--enable-source-maps --max-old-space-size=6000")

    def test_caller_value_wins(self) -> None:
        self.assertEqual(node_options("--max-old-space-size=4096", 64000, 75), "--max-old-space-size=4096")


class TotalTest(unittest.TestCase):
    def test_linux_meminfo(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "meminfo"
            f.write_text("MemFree: 1 kB\nMemTotal:       16384000 kB\n")
            self.assertEqual(total_mib_linux(str(f)), 16000)

    def test_linux_meminfo_missing_total(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "meminfo"
            f.write_text("MemFree: 1 kB\n")
            with self.assertRaises(ValueError):
                total_mib_linux(str(f))

    def test_unknown_platform(self) -> None:
        with self.assertRaises(ValueError):
            total_mib("plan9")

    def test_this_machine_reads(self) -> None:
        self.assertGreater(total_mib(), 0)


class MainTest(unittest.TestCase):
    def run_main(self, env: dict[str, str]) -> tuple[str, str]:
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict("os.environ", env, clear=True), redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(node_heap.main([]), 0)
        return out.getvalue().strip(), err.getvalue()

    def test_prints_options(self) -> None:
        with mock.patch.object(node_heap, "total_mib", return_value=8192):
            out, _ = self.run_main({"NODE_OPTIONS": "--x"})
        self.assertEqual(out, "--x --max-old-space-size=6144")

    def test_unreadable_ram_keeps_existing(self) -> None:
        with mock.patch.object(node_heap, "total_mib", side_effect=OSError("nope")):
            out, err = self.run_main({"NODE_OPTIONS": "--x"})
        self.assertEqual(out, "--x")
        self.assertIn("::warning::", err)


if __name__ == "__main__":
    unittest.main()
