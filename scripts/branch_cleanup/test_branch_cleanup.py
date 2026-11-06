import json
import os
import tempfile
import unittest
import unittest.mock
from urllib.parse import parse_qs, unquote, urlsplit

import branch_cleanup as bc

REPO = "prosopo/demo"


def pr(number, ref, sha, state="closed", merged=True, repo=REPO, base="main"):
    return {"number": number, "state": state, "merged_at": "2026-01-02T00:00:00Z" if merged else None,
            "html_url": f"https://github.com/{REPO}/pull/{number}",
            "head": {"ref": ref, "sha": sha, "repo": {"full_name": repo} if repo else None},
            "base": {"ref": base}}


class FakeGitHub:
    """Answers the handful of gh api calls branch_cleanup makes, from in-memory branches and PRs."""

    def __init__(self, branches, pulls, default="main"):
        self.branches = {b["name"]: b for b in branches}
        self.pulls, self.default = pulls, default
        self.deleted: list[str] = []
        self.moves: dict[str, str] = {}

    def __call__(self, args):
        if args[:3] == ["api", "-X", "DELETE"]:
            self.deleted.append(unquote(args[3].split("/git/refs/heads/", 1)[1]))
            return ""
        path = args[1]
        u = urlsplit(path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        paged = "--paginate" in args
        if u.path == f"repos/{REPO}":
            body = {"default_branch": self.default}
        elif u.path == f"repos/{REPO}/branches":
            body = list(self.branches.values())
        elif u.path.startswith(f"repos/{REPO}/branches/"):
            name = unquote(u.path.split("/branches/", 1)[1])
            sha = self.moves.get(name, self.branches[name]["commit"]["sha"])
            body = {"name": name, "commit": {"sha": sha}}
        elif u.path == f"repos/{REPO}/pulls":
            out = self.pulls
            if q.get("state") in ("open", "closed"):
                out = [p for p in out if p["state"] == q["state"]]
            if "head" in q:
                # GitHub filters head by owner:ref, which also matches a fork PR whose
                # fork lives under the same owner; the script must check head.repo itself.
                _, ref = q["head"].split(":", 1)
                out = [p for p in out if p["head"]["ref"] == ref]
            if "base" in q:
                out = [p for p in out if p["base"]["ref"] == q["base"]]
            body = out
        else:
            raise AssertionError(f"unexpected call {args}")
        return json.dumps([body] if paged else body)


def branch(name, sha, protected=False):
    return {"name": name, "commit": {"sha": sha}, "protected": protected}


class CandidateTests(unittest.TestCase):
    def names(self, fake, keep=("release/*",)):
        return [c.branch for c in bc.Cleaner(REPO, fake, keep).candidates()]

    def test_squash_merged_untouched_is_deleted(self):
        fake = FakeGitHub([branch("main", "m"), branch("feat", "a1")], [pr(1, "feat", "a1")])
        self.assertEqual(self.names(fake), ["feat"])

    def test_commits_after_merge_are_kept(self):
        fake = FakeGitHub([branch("main", "m"), branch("feat", "a2")], [pr(1, "feat", "a1")])
        self.assertEqual(self.names(fake), [])

    def test_open_pr_on_branch_keeps_it(self):
        fake = FakeGitHub([branch("main", "m"), branch("feat", "a1")],
                          [pr(1, "feat", "a1"), pr(2, "feat", "a1", state="open", merged=False)])
        self.assertEqual(self.names(fake), [])

    def test_base_of_open_pr_is_kept(self):
        fake = FakeGitHub([branch("main", "m"), branch("feat", "a1")],
                          [pr(1, "feat", "a1"), pr(2, "child", "c1", state="open", merged=False, base="feat")])
        self.assertEqual(self.names(fake), [])

    def test_protected_default_and_release_are_kept(self):
        fake = FakeGitHub([branch("main", "a1"), branch("prot", "a2", protected=True), branch("release/1", "a3")],
                          [pr(1, "main", "a1"), pr(2, "prot", "a2"), pr(3, "release/1", "a3")])
        self.assertEqual(self.names(fake), [])

    def test_extra_keep_pattern(self):
        fake = FakeGitHub([branch("main", "m"), branch("hotfix/x", "a1")], [pr(1, "hotfix/x", "a1")])
        self.assertEqual(self.names(fake, keep=("hotfix/*",)), [])

    def test_fork_pr_heads_are_ignored(self):
        fake = FakeGitHub([branch("main", "m"), branch("feat", "a1")],
                          [pr(1, "feat", "a1", repo="someone/demo"), pr(2, "feat", "a1", repo=None)])
        self.assertEqual(self.names(fake), [])

    def test_fork_open_pr_does_not_block(self):
        fake = FakeGitHub([branch("main", "m"), branch("feat", "a1")],
                          [pr(1, "feat", "a1"), pr(2, "feat", "f9", state="open", merged=False, repo="x/demo")])
        self.assertEqual(self.names(fake), ["feat"])

    def test_closed_unmerged_and_no_pr_are_kept(self):
        fake = FakeGitHub([branch("main", "m"), branch("closed", "a1"), branch("lonely", "a2")],
                          [pr(1, "closed", "a1", merged=False)])
        self.assertEqual(self.names(fake), [])

    def test_latest_matching_pr_is_reported(self):
        old, new = pr(1, "feat", "a1"), pr(2, "feat", "a1")
        new["merged_at"] = "2026-03-01T00:00:00Z"
        fake = FakeGitHub([branch("main", "m"), branch("feat", "a1")], [old, new])
        self.assertEqual(bc.Cleaner(REPO, fake).candidates()[0].pr_number, 2)

    def test_branch_name_with_slash_is_url_encoded(self):
        calls = []
        fake = FakeGitHub([branch("main", "m"), branch("a/b", "a1")], [pr(1, "a/b", "a1")])

        def spy(args):
            calls.append(args)
            return fake(args)
        self.assertEqual([c.branch for c in bc.Cleaner(REPO, spy).candidates()], ["a/b"])
        self.assertTrue(any("head=prosopo%3Aa%2Fb" in a[1] for a in calls))

    def test_no_colon_reaches_gh_placeholder_expansion(self):
        calls = []
        fake = FakeGitHub([branch("main", "m"), branch("repo-diagram", "a1")], [pr(1, "repo-diagram", "a1")])

        def spy(args):
            calls.append(args)
            return fake(args)
        self.assertEqual([c.branch for c in bc.Cleaner(REPO, spy).candidates()], ["repo-diagram"])
        self.assertFalse(any(":" in a[1] for a in calls if a[0] == "api" and len(a) > 1))


class RunTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeGitHub([branch("main", "m"), branch("feat", "a1"), branch("keep", "b2")],
                               [pr(7, "feat", "a1"), pr(8, "keep", "b1")])

    def run_it(self, dry_run):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "summary.md")
            with open(os.devnull, "w") as null, unittest.mock.patch("sys.stdout", null):
                self.assertEqual(bc.run(REPO, dry_run, ("release/*",), self.fake, path), 0)
            with open(path, encoding="utf-8") as f:
                return f.read()

    def test_dry_run_deletes_nothing_and_lists_candidates(self):
        text = self.run_it(True)
        self.assertEqual(self.fake.deleted, [])
        self.assertIn("dry run", text)
        self.assertIn("`feat` | `a1` | [#7](https://github.com/prosopo/demo/pull/7)", text)
        self.assertNotIn("`keep`", text)

    def test_delete_mode_deletes_only_candidates(self):
        text = self.run_it(False)
        self.assertEqual(self.fake.deleted, ["feat"])
        self.assertIn("| deleted |", text)

    def test_tip_moved_before_delete_is_kept(self):
        self.fake.moves["feat"] = "a9"
        text = self.run_it(False)
        self.assertEqual(self.fake.deleted, [])
        self.assertIn("kept (tip moved)", text)


class GhRetryTests(unittest.TestCase):
    def test_retries_then_succeeds(self):
        calls, sleeps = [], []

        def run(cmd, **kw):
            calls.append(cmd)
            if len(calls) < 3:
                raise bc.subprocess.CalledProcessError(1, cmd, stderr="HTTP 502")
            return bc.subprocess.CompletedProcess(cmd, 0, stdout="ok")
        with unittest.mock.patch("sys.stdout", open(os.devnull, "w")):
            self.assertEqual(bc.gh(["api", "x"], run=run, sleep=sleeps.append), "ok")
        self.assertEqual(sleeps, [2, 5])

    def test_gives_up_after_all_retries(self):
        sleeps = []

        def run(cmd, **kw):
            raise bc.subprocess.CalledProcessError(1, cmd, stderr="HTTP 503")
        with unittest.mock.patch("sys.stdout", open(os.devnull, "w")):
            with self.assertRaises(bc.subprocess.CalledProcessError):
                bc.gh(["api", "x"], run=run, sleep=sleeps.append)
        self.assertEqual(sleeps, list(bc.RETRY_DELAYS))

    def test_terminal_error_is_not_retried(self):
        sleeps = []

        def run(cmd, **kw):
            raise bc.subprocess.CalledProcessError(1, cmd, stderr="unable to expand placeholder in path")
        with unittest.mock.patch("sys.stdout", open(os.devnull, "w")):
            with self.assertRaises(bc.subprocess.CalledProcessError):
                bc.gh(["api", "x"], run=run, sleep=sleeps.append)
        self.assertEqual(sleeps, [])


class CliTests(unittest.TestCase):
    def test_parse_bool(self):
        self.assertTrue(bc.parse_bool("True"))
        self.assertFalse(bc.parse_bool("false"))
        with self.assertRaises(Exception):
            bc.parse_bool("maybe")

    def test_main_defaults_to_dry_run(self):
        seen = {}

        def fake_run(repo, dry_run, keep, summary_path=None):
            seen.update(repo=repo, dry_run=dry_run, keep=keep)
            return 0
        with unittest.mock.patch.object(bc, "run", fake_run):
            self.assertEqual(bc.main(["--repo", REPO, "--keep", "release/*, hotfix/*"]), 0)
        self.assertEqual(seen, {"repo": REPO, "dry_run": True, "keep": ("release/*", "hotfix/*")})


if __name__ == "__main__":
    unittest.main()
