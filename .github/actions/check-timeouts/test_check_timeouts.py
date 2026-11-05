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

UNWRAPPED = """
on: push
jobs:
  a:
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - name: x
        timeout-minutes: 1
        run: |
          echo start
          pnpm install
      - name: inline
        timeout-minutes: 1
        run: docker compose up
"""

COMPOSITE = """
runs:
  using: composite
  steps:
    - name: install
      shell: bash
      run: |
        timeout 5m pnpm install
        npm ci
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
        args = ["--dir", str(self.dir), "--actions-dir", str(self.dir / "actions")]
        self.write("g.yml", GOOD)
        self.assertEqual(ct.main(args), 0)
        self.write("b.yaml", BAD)
        self.assertEqual(ct.main(args), 1)

    def test_main_checks_composite_actions(self) -> None:
        (self.dir / "actions" / "x").mkdir(parents=True)
        self.write("actions/x/action.yml", COMPOSITE)
        self.assertEqual(ct.main(["--dir", str(self.dir), "--actions-dir", str(self.dir / "actions")]), 1)

    def test_unwrapped_command_reported_at_its_line(self) -> None:
        found = ct.problems(self.write("u.yml", UNWRAPPED))
        self.assertEqual(found, [
            f"{self.dir}/u.yml:12: job 'a' step 'x' runs 'pnpm' without timeout"
            " (wrap in `timeout N` or add `# no-timeout: <why>`)",
            f"{self.dir}/u.yml:15: job 'a' step 'inline' runs 'docker' without timeout"
            " (wrap in `timeout N` or add `# no-timeout: <why>`)",
        ])

    def test_composite_action_commands_checked(self) -> None:
        found = ct.action_problems(self.write("action.yml", COMPOSITE))
        self.assertEqual(len(found), 1)
        self.assertIn("action.yml:9: step 'install' runs 'npm'", found[0])

    def test_non_composite_action_ignored(self) -> None:
        self.assertEqual(ct.action_problems(self.write("action.yml", "runs:\n  using: node20\n  main: x.js\n")), [])

    def test_unbounded_commands(self) -> None:
        cases = {
            "pnpm install": ["pnpm"],
            "timeout 5m pnpm install": [],
            "sudo apt-get update": ["apt-get"],
            "sudo timeout 5m apt-get update": [],
            "FOO=1 BAR='a b' cargo test": ["cargo"],
            "echo hi && git fetch origin": ["git fetch"],
            "git status": [],
            "if gh pr view 1; then": ["gh"],
            "x=$(gh api /user)": ["gh"],
            "curl -fsS --max-time 10 https://x": [],
            "curl -fsS https://x | tar xz": ["curl"],
            "pnpm build # no-timeout: bounded by turbo": [],
            "# pnpm install": [],
            "ls | npm ci": ["npm"],
            "pnpm-lock-check": [],
        }
        for script, want in cases.items():
            with self.subTest(script=script):
                self.assertEqual([c for _, c in ct.unbounded_commands(script)], want)

    def test_continuation_and_heredoc_lines_skipped(self) -> None:
        script = "timeout 5m curl -fsSL x \\\n  | docker load\ncat <<EOF\npnpm install\nEOF\nnpm ci\n"
        self.assertEqual(ct.unbounded_commands(script), [(5, "npm")])

    def test_empty_file(self) -> None:
        self.assertEqual(ct.problems(self.write("e.yml", "")), [])


if __name__ == "__main__":
    unittest.main()
