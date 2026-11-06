import argparse
import json
import os
import subprocess
import tempfile
import unittest

import pick_runner as pr

REPO = "prosopo/captcha-private"
SELF = pr.DEFAULT_SELF_HOSTED_LABELS


def decide(**kw: object) -> pr.Decision:
    args: dict[str, object] = {"is_fork": False, "runner_input": "auto", "pr_body": None, "runner_map": "", "workflow": "tests"}
    args.update(kw)
    return pr.decide(**args)  # type: ignore[arg-type]


class Decide(unittest.TestCase):
    def test_map_entry(self) -> None:
        d = decide(runner_map='{"tests":"self-hosted","e2e":"hosted"}')
        self.assertEqual(d.kind, pr.SELF_HOSTED)
        self.assertIn("GH_RUNNER_MAP", d.reason)

    def test_map_entry_hosted(self) -> None:
        self.assertEqual(decide(runner_map='{"tests":"hosted"}').kind, pr.HOSTED)

    def test_missing_entry_is_hosted(self) -> None:
        d = decide(runner_map='{"e2e":"self-hosted"}')
        self.assertEqual(d.kind, pr.HOSTED)
        self.assertEqual(d.warnings, [])

    def test_empty_map_is_hosted(self) -> None:
        self.assertEqual(decide().kind, pr.HOSTED)

    def test_invalid_json_is_hosted_with_warning(self) -> None:
        d = decide(runner_map="{tests: self-hosted")
        self.assertEqual(d.kind, pr.HOSTED)
        self.assertEqual(len(d.warnings), 1)
        self.assertIn("GH_RUNNER_MAP", d.warnings[0])

    def test_non_object_json_is_hosted_with_warning(self) -> None:
        d = decide(runner_map='["self-hosted"]')
        self.assertEqual(d.kind, pr.HOSTED)
        self.assertTrue(d.warnings)

    def test_bad_map_value_warns(self) -> None:
        d = decide(runner_map='{"tests":"gpu"}')
        self.assertEqual(d.kind, pr.HOSTED)
        self.assertTrue(d.warnings)

    def test_fork_beats_everything(self) -> None:
        d = decide(is_fork=True, runner_input="self-hosted", pr_body="runner: self-hosted",
                   runner_map='{"tests":"self-hosted"}')
        self.assertEqual(d.kind, pr.HOSTED)
        self.assertIn("fork", d.reason)

    def test_input_beats_pr_line_and_map(self) -> None:
        d = decide(runner_input="hosted", pr_body="runner: self-hosted", runner_map='{"tests":"self-hosted"}')
        self.assertEqual(d.kind, pr.HOSTED)
        self.assertIn("input", d.reason)
        d = decide(runner_input="self-hosted", pr_body="runner: hosted", runner_map='{"tests":"hosted"}')
        self.assertEqual(d.kind, pr.SELF_HOSTED)

    def test_pr_line_beats_map(self) -> None:
        d = decide(pr_body="Fixes things.\n\nrunner: hosted\n", runner_map='{"tests":"self-hosted"}')
        self.assertEqual(d.kind, pr.HOSTED)
        self.assertIn("PR description", d.reason)

    def test_auto_input_falls_through(self) -> None:
        self.assertEqual(decide(runner_input="auto", runner_map='{"tests":"self-hosted"}').kind, pr.SELF_HOSTED)
        self.assertEqual(decide(runner_input="", runner_map='{"tests":"self-hosted"}').kind, pr.SELF_HOSTED)

    def test_bad_input_and_line_warn_and_fall_through(self) -> None:
        d = decide(runner_input="big", pr_body="runner: huge", runner_map='{"tests":"self-hosted"}')
        self.assertEqual(d.kind, pr.SELF_HOSTED)
        self.assertEqual(len(d.warnings), 2)


class Helpers(unittest.TestCase):
    def test_workflow_key(self) -> None:
        self.assertEqual(pr.workflow_key(f"{REPO}/.github/workflows/tests.yml@refs/pull/1/merge"), "tests")
        self.assertEqual(pr.workflow_key(f"{REPO}/.github/workflows/e2e.yaml@refs/heads/main"), "e2e")
        self.assertEqual(pr.workflow_key(""), "")

    def test_pr_line(self) -> None:
        self.assertEqual(pr.pr_line_kind("a\n  Runner:  Self-Hosted \nb"), "self-hosted")
        self.assertIsNone(pr.pr_line_kind("the runner: is mentioned inline"))
        self.assertIsNone(pr.pr_line_kind(None))

    def test_size_labels_only_for_self_hosted(self) -> None:
        sizes = {"build": ["xlarge"], "lint": ["small"]}
        default, jobs = pr.labels_for(pr.SELF_HOSTED, SELF, sizes)
        self.assertEqual(default, SELF)
        self.assertEqual(jobs["build"], SELF + ["xlarge"])
        default, jobs = pr.labels_for(pr.HOSTED, SELF, sizes)
        self.assertEqual(default, ["ubuntu-latest"])
        self.assertEqual(jobs, {"build": ["ubuntu-latest"], "lint": ["ubuntu-latest"]})

    def test_size_label_not_duplicated(self) -> None:
        _, jobs = pr.labels_for(pr.SELF_HOSTED, ["self-hosted", "xlarge"], {"b": ["xlarge"]})
        self.assertEqual(jobs["b"], ["self-hosted", "xlarge"])

    def test_parse_sizes(self) -> None:
        w: list[str] = []
        self.assertEqual(pr.parse_sizes('{"a":"large","b":["x","y"],"c":3}', w), {"a": ["large"], "b": ["x", "y"]})
        self.assertEqual(len(w), 1)
        w = []
        self.assertEqual(pr.parse_sizes("nope", w), {})
        self.assertEqual(len(w), 1)
        self.assertEqual(pr.parse_sizes("", w), {})

    def test_base_self_hosted(self) -> None:
        w: list[str] = []
        self.assertEqual(pr.base_self_hosted('["self-hosted","arm"]', w), ["self-hosted", "arm"])
        self.assertEqual(pr.base_self_hosted("", w), SELF)
        self.assertEqual(w, [])
        self.assertEqual(pr.base_self_hosted("self-hosted", w), SELF)
        self.assertEqual(len(w), 1)


class Run(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def env(self, event: dict, **extra: str) -> dict[str, str]:
        path = os.path.join(self.dir.name, "event.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(event, fh)
        return {"GITHUB_EVENT_PATH": path, "GITHUB_REPOSITORY": REPO,
                "GITHUB_WORKFLOW_REF": f"{REPO}/.github/workflows/tests.yml@refs/pull/7/merge", **extra}

    @staticmethod
    def args(runner: str = "auto", sizes: str = "", workflow: str = "") -> argparse.Namespace:
        return argparse.Namespace(runner=runner, sizes=sizes, workflow=workflow)

    def test_fork_pr_hosted_and_body_not_fetched(self) -> None:
        def fetch(repo: str, number: int) -> str:
            raise AssertionError("fork PR body must not be read")
        event = {"pull_request": {"number": 7, "head": {"repo": {"full_name": "evil/captcha-private"}}}}
        out = pr.run(self.env(event, GH_RUNNER_MAP='{"tests":"self-hosted"}'), self.args("self-hosted"), fetch)
        self.assertEqual(out["kind"], pr.HOSTED)
        self.assertEqual(json.loads(out["default"]), ["ubuntu-latest"])

    def test_pr_line_read_via_fetch(self) -> None:
        calls: list[tuple[str, int]] = []

        def fetch(repo: str, number: int) -> str:
            calls.append((repo, number))
            return "runner: self-hosted"
        event = {"pull_request": {"number": 7, "body": "stale", "head": {"repo": {"full_name": REPO}}}}
        out = pr.run(self.env(event), self.args(sizes='{"build":"xlarge"}'), fetch)
        self.assertEqual(calls, [(REPO, 7)])
        self.assertEqual(out["kind"], pr.SELF_HOSTED)
        self.assertEqual(json.loads(out["labels"]), {"build": SELF + ["xlarge"]})
        self.assertIn("Reason: 'runner: self-hosted' line", out["summary"])
        self.assertIn("| `build` |", out["summary"])

    def test_fetch_failure_falls_back_to_event_body(self) -> None:
        def fetch(repo: str, number: int) -> str:
            raise subprocess.CalledProcessError(1, ["gh"])
        event = {"pull_request": {"number": 7, "body": "runner: self-hosted", "head": {"repo": {"full_name": REPO}}}}
        out = pr.run(self.env(event), self.args(), fetch)
        self.assertEqual(out["kind"], pr.SELF_HOSTED)
        self.assertIn("could not read", out["summary"])

    def test_push_uses_map_and_workflow_override(self) -> None:
        env = self.env({}, GH_RUNNER_MAP='{"deploy":"self-hosted"}', GH_RUNNER='["self-hosted","vm"]')
        out = pr.run(env, self.args(workflow="deploy"))
        self.assertEqual(out["kind"], pr.SELF_HOSTED)
        self.assertEqual(json.loads(out["default"]), ["self-hosted", "vm"])
        self.assertIn("Workflow key: `deploy`", out["summary"])

    def test_hosted_ignores_unparseable_gh_runner(self) -> None:
        out = pr.run(self.env({}, GH_RUNNER='"ubuntu-24.04"'), self.args("hosted"))
        self.assertNotIn("Warning", out["summary"])
        out = pr.run(self.env({}, GH_RUNNER='"ubuntu-24.04"'), self.args("self-hosted"))
        self.assertEqual(json.loads(out["default"]), SELF)
        self.assertIn("Warning: GH_RUNNER", out["summary"])

    def test_main_writes_output_and_summary(self) -> None:
        out_path = os.path.join(self.dir.name, "out")
        sum_path = os.path.join(self.dir.name, "sum")
        env = self.env({}, GH_RUNNER_MAP="{bad", GITHUB_OUTPUT=out_path, GITHUB_STEP_SUMMARY=sum_path)
        old = dict(os.environ)
        os.environ.update(env)
        try:
            self.assertEqual(pr.main(["--sizes", '{"build":["xlarge"]}']), 0)
        finally:
            os.environ.clear()
            os.environ.update(old)
        with open(out_path, encoding="utf-8") as fh:
            outputs = dict(line.rstrip("\n").split("=", 1) for line in fh)
        self.assertEqual(outputs["kind"], "hosted")
        self.assertEqual(json.loads(outputs["labels"]), {"build": ["ubuntu-latest"]})
        with open(sum_path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("### Runner: hosted", text)
        self.assertIn("Warning: GH_RUNNER_MAP is not a valid JSON object", text)


if __name__ == "__main__":
    unittest.main()
