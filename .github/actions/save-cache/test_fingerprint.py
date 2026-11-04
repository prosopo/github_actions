import os
import tempfile
import unittest
from pathlib import Path

from fingerprint import fingerprint


class FingerprintTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "d/sub").mkdir(parents=True)
        (self.root / "d/sub/a").write_text("1")
        (self.root / "f").write_text("2")
        self.paths = [str(self.root / "d"), str(self.root / "f")]

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_stable(self) -> None:
        self.assertEqual(fingerprint(self.paths), fingerprint(self.paths))

    def test_new_file_changes_it(self) -> None:
        before = fingerprint(self.paths)
        (self.root / "d/b").write_text("3")
        self.assertNotEqual(before, fingerprint(self.paths))

    def test_mtime_changes_it(self) -> None:
        before = fingerprint(self.paths)
        os.utime(self.root / "f", (1, 1))
        self.assertNotEqual(before, fingerprint(self.paths))

    def test_missing_path_is_not_an_error(self) -> None:
        self.assertEqual(len(fingerprint([str(self.root / "nope")])), 64)


if __name__ == "__main__":
    unittest.main()
