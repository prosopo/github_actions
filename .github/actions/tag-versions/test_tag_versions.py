import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import tag_versions
from tag_versions import Unit


def run(*args: str, cwd: str) -> str:
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True, timeout=30).stdout.strip()


class Repo:
    def __init__(self, path: str) -> None:
        self.path = path
        run("git", "init", "-q", "-b", "main", cwd=path)
        run("git", "config", "user.email", "t@example.com", cwd=path)
        run("git", "config", "user.name", "t", cwd=path)

    def write(self, rel: str, data: object) -> None:
        target = Path(self.path, rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data) if not isinstance(data, str) else data)

    def package(self, rel: str, name: str, version: str) -> None:
        self.write(f"{rel}/package.json" if rel else "package.json", {"name": name, "version": version})

    def commit(self) -> str:
        run("git", "add", "-A", cwd=self.path)
        run("git", "commit", "-q", "--allow-empty", "-m", "c", cwd=self.path)
        return run("git", "rev-parse", "HEAD", cwd=self.path)

    def tags(self) -> list[str]:
        return sorted(run("git", "tag", "--list", cwd=self.path).split())


class TestTagsFor(unittest.TestCase):
    def test_unchanged_version_is_not_tagged(self) -> None:
        units = {"a/package.json": Unit("@prosopo/a", "1.0.0")}
        self.assertEqual(tag_versions.tags_for(units, units, []), [])

    def test_changed_version_is_tagged_unscoped(self) -> None:
        before = {"a/package.json": Unit("@prosopo/a", "1.0.0")}
        after = {"a/package.json": Unit("@prosopo/a", "1.1.0")}
        self.assertEqual(tag_versions.tags_for(before, after, []), ["a-v1.1.0"])

    def test_new_package_is_tagged(self) -> None:
        self.assertEqual(tag_versions.tags_for({}, {"b/package.json": Unit("b", "0.1.0")}, []), ["b-v0.1.0"])

    def test_moved_package_keeps_identity_by_name(self) -> None:
        before = {"old/package.json": Unit("@prosopo/a", "1.0.0")}
        after = {"new/package.json": Unit("@prosopo/a", "1.0.0")}
        self.assertEqual(tag_versions.tags_for(before, after, []), [])

    def test_fixed_group_gets_one_tag_named_after_first_entry(self) -> None:
        groups = [["@prosopo/procaptcha", "@prosopo/*"]]
        before = {"x/package.json": Unit("@prosopo/x", "3.0.0"), "y/package.json": Unit("@prosopo/y", "3.0.0")}
        after = {"x/package.json": Unit("@prosopo/x", "3.0.1"), "y/package.json": Unit("@prosopo/y", "3.0.1")}
        self.assertEqual(tag_versions.tags_for(before, after, groups), ["procaptcha-v3.0.1"])

    def test_package_outside_group_tags_alone(self) -> None:
        groups = [["@prosopo/x"]]
        after = {"x/package.json": Unit("@prosopo/x", "2.0.0"), "z/package.json": Unit("z", "1.0.0")}
        self.assertEqual(tag_versions.tags_for({}, after, groups), ["x-v2.0.0", "z-v1.0.0"])


class TestFixedGroups(unittest.TestCase):
    def test_missing_config(self) -> None:
        self.assertEqual(tag_versions.fixed_groups(None), [])

    def test_empty_groups_dropped(self) -> None:
        self.assertEqual(tag_versions.fixed_groups(json.dumps({"fixed": [[], ["a", "b"]]})), [["a", "b"]])


class TestMain(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Repo(self.tmp.name)
        self.cwd = os.getcwd()
        os.chdir(self.tmp.name)
        self.output = Path(self.tmp.name, "..", f"out-{os.getpid()}-{id(self)}").resolve()
        os.environ["GITHUB_OUTPUT"] = str(self.output)

    def tearDown(self) -> None:
        os.chdir(self.cwd)
        os.environ.pop("GITHUB_OUTPUT", None)
        self.output.unlink(missing_ok=True)
        self.tmp.cleanup()

    def test_tags_only_the_changed_package(self) -> None:
        self.repo.package("a", "@prosopo/a", "1.0.0")
        self.repo.package("b", "@prosopo/b", "1.0.0")
        self.repo.write("node_modules/c/package.json", {"name": "c", "version": "9.0.0"})
        before = self.repo.commit()
        self.repo.package("a", "@prosopo/a", "1.0.1")
        after = self.repo.commit()
        self.assertEqual(tag_versions.main(["--before", before, "--after", after]), 0)
        self.assertEqual(self.repo.tags(), ["a-v1.0.1"])
        self.assertIn('tags=["a-v1.0.1"]', self.output.read_text())

    def test_unrelated_push_tags_nothing(self) -> None:
        self.repo.package("a", "a", "1.0.0")
        before = self.repo.commit()
        self.repo.write("README", "changed")
        after = self.repo.commit()
        self.assertEqual(tag_versions.main(["--before", before, "--after", after]), 0)
        self.assertEqual(self.repo.tags(), [])

    def test_zero_before_tags_nothing(self) -> None:
        self.repo.package("a", "a", "1.0.0")
        after = self.repo.commit()
        self.assertEqual(tag_versions.main(["--before", tag_versions.ZERO_SHA, "--after", after]), 0)
        self.assertEqual(self.repo.tags(), [])
        self.assertIn("tags=[]", self.output.read_text())

    def test_unknown_before_tags_nothing(self) -> None:
        self.repo.package("a", "a", "1.0.0")
        after = self.repo.commit()
        self.assertEqual(tag_versions.main(["--before", "1" * 40, "--after", after]), 0)
        self.assertEqual(self.repo.tags(), [])

    def test_rerun_is_a_no_op(self) -> None:
        self.repo.package("a", "a", "1.0.0")
        before = self.repo.commit()
        self.repo.package("a", "a", "2.0.0")
        after = self.repo.commit()
        tag_versions.main(["--before", before, "--after", after])
        self.output.write_text("")
        self.assertEqual(tag_versions.main(["--before", before, "--after", after]), 0)
        self.assertIn("tags=[]", self.output.read_text())

    def test_fixed_group_from_changeset_config(self) -> None:
        self.repo.write(".changeset/config.json", {"fixed": [["@prosopo/procaptcha", "@prosopo/x"]]})
        self.repo.package("p", "@prosopo/procaptcha", "3.0.0")
        self.repo.package("x", "@prosopo/x", "3.0.0")
        before = self.repo.commit()
        self.repo.package("p", "@prosopo/procaptcha", "3.0.1")
        self.repo.package("x", "@prosopo/x", "3.0.1")
        after = self.repo.commit()
        tag_versions.main(["--before", before, "--after", after])
        self.assertEqual(self.repo.tags(), ["procaptcha-v3.0.1"])

    def test_push_sends_new_tags_to_remote(self) -> None:
        with tempfile.TemporaryDirectory() as remote:
            run("git", "init", "-q", "--bare", remote, cwd=remote)
            run("git", "remote", "add", "origin", remote, cwd=self.tmp.name)
            self.repo.package("a", "a", "1.0.0")
            before = self.repo.commit()
            self.repo.package("a", "a", "1.1.0")
            after = self.repo.commit()
            self.assertEqual(tag_versions.main(["--before", before, "--after", after, "--push"]), 0)
            self.assertEqual(run("git", "tag", "--list", cwd=remote), "a-v1.1.0")

    def test_push_failure_exits_non_zero(self) -> None:
        run("git", "remote", "add", "origin", "/nonexistent/remote", cwd=self.tmp.name)
        self.repo.package("a", "a", "1.0.0")
        before = self.repo.commit()
        self.repo.package("a", "a", "1.1.0")
        after = self.repo.commit()
        self.assertEqual(tag_versions.main(["--before", before, "--after", after, "--push"]), 1)


if __name__ == "__main__":
    unittest.main()
