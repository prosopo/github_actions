import tempfile
import unittest
from pathlib import Path

import run_python_tests

PASSING = "import unittest\nclass T(unittest.TestCase):\n    def test_ok(self):\n        pass\n"
FAILING = "import unittest\nclass T(unittest.TestCase):\n    def test_bad(self):\n        self.fail()\n"


class RunPythonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write(self, rel: str, body: str) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)

    def test_finds_each_dir_once_and_skips_fixtures(self) -> None:
        self.write("a/test_one.py", PASSING)
        self.write("a/test_two.py", PASSING)
        self.write("b/c/test_three.py", PASSING)
        self.write("fixtures/x/test_skip.py", FAILING)
        self.assertEqual(run_python_tests.test_dirs(self.root), [self.root / "a", self.root / "b/c"])

    def test_passes_when_all_pass(self) -> None:
        self.write("a/test_one.py", PASSING)
        self.assertEqual(run_python_tests.main(["--root", str(self.root)]), 0)

    def test_fails_when_any_dir_fails(self) -> None:
        self.write("a/test_one.py", PASSING)
        self.write("b/test_two.py", FAILING)
        self.assertEqual(run_python_tests.main(["--root", str(self.root)]), 1)

    def test_fails_when_no_tests_found(self) -> None:
        self.assertEqual(run_python_tests.main(["--root", str(self.root)]), 1)


if __name__ == "__main__":
    unittest.main()
