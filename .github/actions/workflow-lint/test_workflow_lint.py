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


class TestNodeVersion(unittest.TestCase):
    STEP = "      - run: echo ok\n"

    def with_step(self, body: str) -> str:
        return COMPLIANT.replace(self.STEP, "      - uses: actions/setup-node@v6\n        with:\n" + body + self.STEP, 1)

    def test_literal_node_version(self) -> None:
        self.assertEqual(rules(self.with_step("          node-version: '24.22.1'\n")), ["node-version"])

    def test_bare_number_node_version(self) -> None:
        self.assertEqual(rules(self.with_step("          node-version: 24\n")), ["node-version"])

    def test_node_version_file_passes(self) -> None:
        self.assertEqual(rules(self.with_step("          node-version-file: .nvmrc\n")), [])

    def test_matrix_expression_passes(self) -> None:
        self.assertEqual(rules(self.with_step("          node-version: ${{ matrix.node }}\n")), [])

    def test_waiver(self) -> None:
        body = "          # workflow-lint: allow node-version -- floor test\n          node-version: '22.12'\n"
        self.assertEqual(rules(self.with_step(body)), [])


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


PR_TRIGGER = "  pull_request:\n    types: [opened, synchronize, reopened, ready_for_review, converted_to_draft]\n"


class TestNoPrTarget(unittest.TestCase):
    def test_mapping_form(self) -> None:
        found = lint(COMPLIANT.replace(PR_TRIGGER, PR_TRIGGER + "  pull_request_target:\n    types: [opened]\n"))
        self.assertEqual([(f.rule, f.line) for f in found], [("no-pr-target", 5)])

    def test_string_form(self) -> None:
        text = COMPLIANT.replace("on:\n" + PR_TRIGGER + "  push:\n    branches: [main]\n", "on: pull_request_target\n")
        self.assertEqual(rules(text), ["no-pr-target"])

    def test_list_form(self) -> None:
        text = COMPLIANT.replace("on:\n" + PR_TRIGGER + "  push:\n    branches: [main]\n", "on: [push, pull_request_target]\n")
        self.assertEqual(rules(text), ["no-pr-target"])

    def test_fork_guard_does_not_exempt(self) -> None:
        text = COMPLIANT.replace(PR_TRIGGER, "  pull_request_target:\n").replace(
            "github.event.pull_request.draft != true", "github.event.pull_request.head.repo.full_name == github.repository"
        )
        self.assertEqual(rules(text), ["no-pr-target"])

    def test_waiver_does_not_exempt(self) -> None:
        waived = "  # workflow-lint: allow no-pr-target -- needs secrets\n  pull_request_target:\n"
        text = COMPLIANT.replace(PR_TRIGGER, PR_TRIGGER + waived)
        self.assertEqual(rules(text), ["no-pr-target"])

    def test_name_outside_on_block_passes(self) -> None:
        self.assertEqual(rules(COMPLIANT.replace("echo ok\n  after", "echo pull_request_target\n  after")), [])

    def test_clean_workflow_passes(self) -> None:
        self.assertNotIn("no-pr-target", rules(COMPLIANT))


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
            "      # workflow-lint: allow allow-dirty -- build writes generated files cargo checks\n"
            "      - run: cargo publish --allow-dirty\n  after",
        )
        self.assertEqual(rules(text), [])

    def test_waiver_on_same_line(self) -> None:
        waived = "cargo publish --allow-dirty  # workflow-lint: allow allow-dirty -- generated files\n  after"
        text = COMPLIANT.replace("echo ok\n  after", waived)
        self.assertEqual(rules(text), [])

    def test_waiver_without_reason_is_a_finding(self) -> None:
        text = COMPLIANT.replace("echo ok\n  after", "cargo publish --allow-dirty  # workflow-lint: allow allow-dirty\n  after")
        self.assertEqual(sorted(rules(text)), ["allow-dirty", "waiver"])

    def test_waiver_only_covers_named_rule(self) -> None:
        text = COMPLIANT.replace("echo ok\n  after", "cargo publish --allow-dirty  # workflow-lint: allow literal-main -- n/a\n  after")
        self.assertEqual(rules(text), ["allow-dirty"])


def image_lint(name: str, text: str) -> list[str]:
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / name
        path.write_text(textwrap.dedent(text))
        return [f.message.split(" ", 1)[0] for f in workflow_lint.image_problems(path)]


class TestDockerImagePins(unittest.TestCase):
    def test_compose_latest_and_untagged_fail(self) -> None:
        text = """\
            services:
              a:
                image: caddy:latest
              b:
                image: "ghcr.io/org/app"
              c:
                image: headscale/headscale:v0.28.0
              d:
                image: redis@sha256:abc
              e:
                image: ${IMAGE}
              f:
                image: localhost:5000/app:1.2
            """
        self.assertEqual(image_lint("docker-compose.yml", text), ["caddy:latest", "ghcr.io/org/app"])

    def test_dockerfile_untagged_from_fails(self) -> None:
        text = """\
            FROM node AS build
            FROM --platform=linux/amd64 nginx:1.27.3
            FROM build AS final
            FROM scratch
            FROM ${BASE}
            FROM alpine:latest
            """
        self.assertEqual(image_lint("Dockerfile", text), ["node", "alpine:latest"])

    def test_docker_waiver_with_reason(self) -> None:
        text = "services:\n  a:\n    # workflow-lint: allow image-pin -- local dev only\n    image: app\n"
        self.assertEqual(image_lint("compose.yaml", text), [])

    def test_docker_files_found_outside_node_modules(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            for rel in ("docker/Dockerfile.dev", "x/docker-compose.test.yml", "node_modules/p/Dockerfile", "x/other.yml"):
                Path(d, rel).parent.mkdir(parents=True, exist_ok=True)
                Path(d, rel).write_text("")
            found = [p.relative_to(d).as_posix() for p in workflow_lint.image_files(Path(d))]
            self.assertEqual(found, ["docker/Dockerfile.dev", "x/docker-compose.test.yml"])

    def test_docker_main_fails_on_images_root(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            wf = Path(d, "wf")
            wf.mkdir()
            Path(wf, "ok.yml").write_text(COMPLIANT)
            Path(d, "Dockerfile").write_text("FROM node:22.12.0\n")
            self.assertEqual(workflow_lint.main(["--dir", str(wf), "--images-root", d]), 0)
            Path(d, "Dockerfile").write_text("FROM node\n")
            self.assertEqual(workflow_lint.main(["--dir", str(wf), "--images-root", d]), 1)
            self.assertEqual(workflow_lint.main(["--dir", str(wf)]), 0)


class TestMain(unittest.TestCase):
    def test_exit_codes(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            Path(d, "ok.yml").write_text(COMPLIANT)
            self.assertEqual(workflow_lint.main(["--dir", d]), 0)
            Path(d, "bad.yml").write_text(COMPLIANT.replace("permissions: {}\n", ""))
            self.assertEqual(workflow_lint.main(["--dir", d]), 1)


if __name__ == "__main__":
    unittest.main()
