import tempfile
import textwrap
import unittest
from pathlib import Path

import workflow_lint

COMPLIANT = """\
name: ci
on:
  pull_request:
    types: [opened, synchronize, reopened, ready_for_review, converted_to_draft]
  push:
    branches: [main]
concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: true
permissions: {}
defaults:
  run:
    shell: bash --noprofile --norc -euo pipefail {0}
jobs:
  test:
    if: github.event.pull_request.draft != true
    permissions:
      contents: read
    runs-on: ubuntu-latest
    steps:
      - run: echo ok
  after:
    needs: test
    runs-on: ubuntu-latest
    steps:
      - run: echo ok
"""


def lint(text: str) -> list[workflow_lint.Finding]:
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "wf.yml"
        path.write_text(textwrap.dedent(text))
        return workflow_lint.problems(path)


def rules(text: str) -> list[str]:
    return [f.rule for f in lint(text)]


class TestCompliant(unittest.TestCase):
    def test_compliant_workflow_has_no_findings(self) -> None:
        self.assertEqual(lint(COMPLIANT), [])


class TestConcurrency(unittest.TestCase):
    def test_missing(self) -> None:
        text = COMPLIANT.replace("concurrency:\n  group: ci-${{ github.ref }}\n  cancel-in-progress: true\n", "")
        self.assertEqual(rules(text), ["concurrency"])

    def test_workflow_call_uses_github_workflow(self) -> None:
        text = COMPLIANT.replace("  push:\n", "  workflow_call:\n  push:\n").replace(
            "ci-${{ github.ref }}", "${{ github.workflow }}-${{ inputs.concurrency-key }}"
        )
        self.assertEqual(rules(text), ["concurrency"])

    def test_workflow_call_without_key_input(self) -> None:
        text = COMPLIANT.replace("  push:\n", "  workflow_call:\n  push:\n")
        self.assertEqual(rules(text), ["concurrency"])

    def test_workflow_call_with_key_input_passes(self) -> None:
        text = COMPLIANT.replace("  push:\n", "  workflow_call:\n  push:\n").replace(
            "ci-${{ github.ref }}", "ci-${{ inputs.concurrency-key || github.ref }}"
        )
        self.assertEqual(rules(text), [])


class TestShell(unittest.TestCase):
    def test_missing(self) -> None:
        text = COMPLIANT.replace("defaults:\n  run:\n    shell: bash --noprofile --norc -euo pipefail {0}\n", "")
        self.assertEqual(rules(text), ["shell"])


class TestPrTypes(unittest.TestCase):
    def test_missing_types(self) -> None:
        text = COMPLIANT.replace(", converted_to_draft", "")
        found = lint(text)
        self.assertEqual([f.rule for f in found], ["pr-types"])
        self.assertIn("converted_to_draft", found[0].message)

    def test_root_job_without_draft_guard(self) -> None:
        text = COMPLIANT.replace("    if: github.event.pull_request.draft != true\n", "")
        self.assertEqual(rules(text), ["pr-types"])

    def test_dependent_job_needs_no_guard(self) -> None:
        self.assertNotIn("pr-types", rules(COMPLIANT))


class TestPrTarget(unittest.TestCase):
    def test_no_fork_guard(self) -> None:
        text = COMPLIANT.replace("  pull_request:\n    types: [opened, synchronize, reopened, ready_for_review, converted_to_draft]\n", "  pull_request_target:\n")
        self.assertEqual(rules(text), ["pr-target", "pr-target"])

    def test_fork_guard_passes(self) -> None:
        text = COMPLIANT.replace("  pull_request:\n    types: [opened, synchronize, reopened, ready_for_review, converted_to_draft]\n", "  pull_request_target:\n").replace(
            "github.event.pull_request.draft != true", "github.event.pull_request.head.repo.full_name == github.repository"
        ).replace("    needs: test\n", "    needs: test\n    if: github.event.pull_request.head.repo.full_name == github.repository\n")
        self.assertEqual(rules(text), [])


class TestPermissions(unittest.TestCase):
    def test_missing_top_level(self) -> None:
        self.assertEqual(rules(COMPLIANT.replace("permissions: {}\n", "")), ["permissions"])

    def test_top_level_write(self) -> None:
        self.assertEqual(rules(COMPLIANT.replace("permissions: {}\n", "permissions:\n  contents: write\n")), ["permissions"])

    def test_top_level_write_all(self) -> None:
        self.assertEqual(rules(COMPLIANT.replace("permissions: {}\n", "permissions: write-all\n")), ["permissions"])

    def test_top_level_read_passes(self) -> None:
        self.assertEqual(rules(COMPLIANT.replace("permissions: {}\n", "permissions:\n  contents: read\n")), [])

    def test_job_write_all(self) -> None:
        text = COMPLIANT.replace("    permissions:\n      contents: read\n", "    permissions: write-all\n")
        self.assertEqual(rules(text), ["permissions"])


class TestText(unittest.TestCase):
    def test_allow_dirty(self) -> None:
        self.assertEqual(rules(COMPLIANT.replace("echo ok\n  after", "cargo publish --allow-dirty\n  after")), ["allow-dirty"])

    def test_literal_main_in_job(self) -> None:
        self.assertEqual(rules(COMPLIANT.replace("echo ok\n  after", "git diff origin/main\n  after")), ["literal-main"])

    def test_literal_main_in_branch_filter_passes(self) -> None:
        self.assertNotIn("literal-main", rules(COMPLIANT))

    def test_secret_to_file(self) -> None:
        text = COMPLIANT.replace("echo ok\n  after", 'echo "${{ secrets.TOKEN }}" > key\n  after')
        self.assertEqual(rules(text), ["secret-to-file"])

    def test_secret_to_github_env(self) -> None:
        text = COMPLIANT.replace("echo ok\n  after", 'echo "T=${{ secrets.TOKEN }}" >> "$GITHUB_ENV"\n  after')
        self.assertEqual(rules(text), ["secret-to-file"])

    def test_commented_out_line_ignored(self) -> None:
        text = COMPLIANT.replace("echo ok\n  after", "echo ok  # not cargo --allow-dirty\n  after")
        self.assertEqual(rules(text), [])

    def test_finding_reports_line(self) -> None:
        found = lint(COMPLIANT.replace("echo ok\n  after", "cargo package --allow-dirty\n  after"))
        self.assertEqual(found[0].line, 21)


class TestWaiver(unittest.TestCase):
    def test_waiver_on_line_above(self) -> None:
        text = COMPLIANT.replace(
            "      - run: echo ok\n  after",
            "      # workflow-lint: allow allow-dirty -- build writes generated files cargo checks\n      - run: cargo publish --allow-dirty\n  after",
        )
        self.assertEqual(rules(text), [])

    def test_waiver_on_same_line(self) -> None:
        text = COMPLIANT.replace("echo ok\n  after", "cargo publish --allow-dirty  # workflow-lint: allow allow-dirty -- generated files\n  after")
        self.assertEqual(rules(text), [])

    def test_waiver_without_reason_is_a_finding(self) -> None:
        text = COMPLIANT.replace("echo ok\n  after", "cargo publish --allow-dirty  # workflow-lint: allow allow-dirty\n  after")
        self.assertEqual(sorted(rules(text)), ["allow-dirty", "waiver"])

    def test_waiver_only_covers_named_rule(self) -> None:
        text = COMPLIANT.replace("echo ok\n  after", "cargo publish --allow-dirty  # workflow-lint: allow literal-main -- n/a\n  after")
        self.assertEqual(rules(text), ["allow-dirty"])


class TestMain(unittest.TestCase):
    def test_exit_codes(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            Path(d, "ok.yml").write_text(COMPLIANT)
            self.assertEqual(workflow_lint.main(["--dir", d]), 0)
            Path(d, "bad.yml").write_text(COMPLIANT.replace("permissions: {}\n", ""))
            self.assertEqual(workflow_lint.main(["--dir", d]), 1)


if __name__ == "__main__":
    unittest.main()
