import os
import tempfile
import time
import unittest
from pathlib import Path

import cache_maintenance as cm

DAY = 86400


def touch(path: Path, size: int, age_days: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    t = time.time() - age_days * DAY
    os.utime(path, (t, t))


class PlanTest(unittest.TestCase):
    def e(self, name: str, age: float, size: int) -> cm.Entry:
        return cm.Entry(Path(name), 1000 * DAY - age * DAY, size)

    def test_old_entries_always_go(self) -> None:
        doomed = cm.plan([self.e("old", 20, 1), self.e("new", 1, 1)], 1000 * DAY, 14, 100, 0, 100)
        self.assertEqual([d.path.name for d in doomed], ["old"])

    def test_size_limit_evicts_oldest_first(self) -> None:
        entries = [self.e("a", 3, 50), self.e("b", 1, 50), self.e("c", 2, 50)]
        doomed = cm.plan(entries, 1000 * DAY, 14, 100, 0, 1000)
        self.assertEqual([d.path.name for d in doomed], ["a"])

    def test_free_space_limit(self) -> None:
        entries = [self.e("a", 3, 50), self.e("b", 1, 50)]
        doomed = cm.plan(entries, 1000 * DAY, 14, 1000, 120, 10)
        self.assertEqual([d.path.name for d in doomed], ["a", "b"])

    def test_nothing_to_do(self) -> None:
        self.assertEqual(cm.plan([self.e("a", 1, 1)], 1000 * DAY, 14, 10, 0, 10), [])


class ParseTest(unittest.TestCase):
    def test_depth(self) -> None:
        self.assertEqual(cm.parse_target("/x/y:3"), (Path("/x/y"), 3))

    def test_default_depth(self) -> None:
        self.assertEqual(cm.parse_target("/x/y"), (Path("/x/y"), 1))

    def test_home(self) -> None:
        self.assertEqual(cm.parse_target("~/a:2")[0], Path.home() / "a")


class MainTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.stamp = self.root / "stamp"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_main(self, *extra: str) -> int:
        return cm.main(["--targets", f"{self.root / 'reg'}:2", "--stamp", str(self.stamp),
                        "--min-free-gb", "0", *extra])

    def test_evicts_whole_entries_by_newest_use(self) -> None:
        touch(self.root / "reg/idx/stale/a", 10, 30)
        touch(self.root / "reg/idx/fresh/a", 10, 30)
        touch(self.root / "reg/idx/fresh/b", 10, 1)
        self.assertEqual(self.run_main(), 0)
        self.assertFalse((self.root / "reg/idx/stale").exists())
        self.assertTrue((self.root / "reg/idx/fresh/a").exists())
        self.assertTrue(self.stamp.exists())

    def test_stamp_skips_recent_run(self) -> None:
        touch(self.root / "reg/idx/stale/a", 10, 30)
        self.stamp.touch()
        self.run_main()
        self.assertTrue((self.root / "reg/idx/stale").exists())

    def test_old_stamp_runs(self) -> None:
        touch(self.root / "reg/idx/stale/a", 10, 30)
        touch(self.stamp, 0, 1)
        self.run_main()
        self.assertFalse((self.root / "reg/idx/stale").exists())

    def test_dry_run_keeps_everything(self) -> None:
        touch(self.root / "reg/idx/stale/a", 10, 30)
        self.run_main("--dry-run")
        self.assertTrue((self.root / "reg/idx/stale").exists())
        self.assertFalse(self.stamp.exists())

    def test_missing_target_is_fine(self) -> None:
        self.assertEqual(cm.main(["--targets", str(self.root / "nope:1"), "--stamp", str(self.stamp)]), 0)


if __name__ == "__main__":
    unittest.main()
