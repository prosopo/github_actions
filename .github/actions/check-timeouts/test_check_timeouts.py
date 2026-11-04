import tempfile
import unittest
from pathlib import Path

import check_timeouts as ct

GOOD = """
on: push
jobs:
  a:
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - name: x
        timeout-minutes: 1
        run: echo
  call:
    uses: o/r/.github/workflows/x.yml@abc
"""

BAD = """
on: push
jobs:
  a:
    runs-on: ubuntu-latest
    steps:
      - name: x
        timeout-minutes: 1
        run: echo
      - uses: actions/checkout@v4
      - run: echo
"""


class CheckTimeoutsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write(self, name: str, text: str) -> Path:
        path = self.dir / name
        path.write_text(text)
        return path

    def test_good_file_passes(self) -> None:
        self.assertEqual(ct.problems(self.write("g.yml", GOOD)), [])

    def test_missing_job_and_steps_reported_with_lines(self) -> None:
        found = ct.problems(self.write("b.yml", BAD))
        self.assertEqual(len(found), 3)
        self.assertIn("b.yml:5: job 'a' has no timeout-minutes", found[0])
        self.assertIn("step 'actions/checkout@v4'", found[1])
        self.assertIn("step '#3'", found[2])

    def test_main_exit_codes(self) -> None:
        self.write("g.yml", GOOD)
        self.assertEqual(ct.main(["--dir", str(self.dir)]), 0)
        self.write("b.yaml", BAD)
        self.assertEqual(ct.main(["--dir", str(self.dir)]), 1)

    def test_empty_file(self) -> None:
        self.assertEqual(ct.problems(self.write("e.yml", "")), [])


if __name__ == "__main__":
    unittest.main()
