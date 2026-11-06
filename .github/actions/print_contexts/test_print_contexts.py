import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from print_contexts import docker_logins, render, run


def fake_runner(cmd: list[str]) -> str:
    return "out:" + " ".join(cmd)


class RenderTest(unittest.TestCase):
    def test_secret_values_masked_and_only_names_printed(self) -> None:
        env = {"SECRETS_CONTEXT": json.dumps({"NPM_TOKEN": "npm_abc", "KEY": "l1\nl2"})}
        out = render(env, [], fake_runner)
        self.assertEqual(out[:3], ["::add-mask::l1", "::add-mask::l2", "::add-mask::npm_abc"])
        body = "\n".join(out[3:])
        self.assertIn('"NPM_TOKEN"', body)
        self.assertNotIn("npm_abc", body)

    def test_env_names_only(self) -> None:
        out = "\n".join(render({"ENV_CONTEXT": json.dumps({"FOO": "bar"})}, [], fake_runner))
        self.assertIn('"FOO"', out)
        self.assertNotIn("bar", out)

    def test_github_token_dropped(self) -> None:
        out = "\n".join(render({"GITHUB_CONTEXT": json.dumps({"token": "ghs_x", "repository": "a/b"})}, [], fake_runner))
        self.assertNotIn("ghs_x", out)
        self.assertIn("a/b", out)

    def test_groups_balanced_and_unset_contexts_skipped(self) -> None:
        out = render({"VARS_CONTEXT": "{}", "INPUTS_CONTEXT": "not json"}, ["npm"], fake_runner)
        self.assertEqual([line for line in out if line.startswith("::group::")], ["::group::vars", "::group::inputs", "::group::npm"])
        self.assertEqual(out.count("::endgroup::"), 3)

    def test_probe_output_and_unknown_probe(self) -> None:
        out = "\n".join(render({}, ["gh", "nope"], fake_runner))
        self.assertIn("gh user: out:gh api user --jq .login", out)
        self.assertIn("unknown probe", out)


class RunTest(unittest.TestCase):
    def test_missing_binary_does_not_raise(self) -> None:
        self.assertIn("not installed", run(["definitely-not-a-binary-xyz"]))

    def test_failure_reports_exit(self) -> None:
        self.assertIn("exit 3", run([sys.executable, "-c", "import sys; sys.exit(3)"]))


class DockerLoginsTest(unittest.TestCase):
    def test_registry_names_without_auth(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "config.json"
            cfg.write_text(json.dumps({"auths": {"ghcr.io": {"auth": "c2VjcmV0"}, "docker.io": {}}}))
            out = docker_logins(cfg)
        self.assertEqual(out, "docker.io\nghcr.io")

    def test_missing_config(self) -> None:
        self.assertIn("no docker config", docker_logins(Path("/nonexistent/config.json")))


if __name__ == "__main__":
    unittest.main()
