import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from branch_cleanup import candidates, main

REPO = "prosopo/x"


def pr(number: int, ref: str, sha: str, state: str = "closed", merged: bool = True, repo: str = REPO) -> dict:
    return {"number": number, "state": state, "merged_at": "2026-01-01" if merged else None,
            "url": f"https://github.com/{REPO}/pull/{number}", "head_ref": ref, "head_sha": sha, "head_repo": repo}


def branch(name: str, sha: str, protected: bool = False) -> dict:
    return {"name": name, "sha": sha, "protected": protected}


class CandidatesTest(unittest.TestCase):
    def names(self, branches: list[dict], prs: list[dict]) -> list[str]:
        return [c.branch for c in candidates(REPO, "main", branches, prs)]

    def test_merged_untouched_is_candidate(self) -> None:
        self.assertEqual(self.names([branch("feat", "a1")], [pr(1, "feat", "a1")]), ["feat"])

    def test_pushed_after_merge_kept(self) -> None:
        self.assertEqual(self.names([branch("feat", "b2")], [pr(1, "feat", "a1")]), [])

    def test_unmerged_closed_kept(self) -> None:
        self.assertEqual(self.names([branch("feat", "a1")], [pr(1, "feat", "a1", merged=False)]), [])

    def test_open_pr_reusing_branch_kept(self) -> None:
        prs = [pr(1, "feat", "a1"), pr(2, "feat", "a1", state="open", merged=False)]
        self.assertEqual(self.names([branch("feat", "a1")], prs), [])

    def test_default_protected_release_kept(self) -> None:
        branches = [branch("main", "m"), branch("prot", "p", protected=True), branch("release/1", "r")]
        prs = [pr(1, "main", "m"), pr(2, "prot", "p"), pr(3, "release/1", "r")]
        self.assertEqual(self.names(branches, prs), [])

    def test_fork_pr_with_same_ref_ignored(self) -> None:
        self.assertEqual(self.names([branch("feat", "a1")], [pr(1, "feat", "a1", repo="someone/x")]), [])

    def test_any_matching_merged_pr_counts(self) -> None:
        prs = [pr(1, "feat", "old"), pr(2, "feat", "a1")]
        found = candidates(REPO, "main", [branch("feat", "a1")], prs)
        self.assertEqual([c.pr for c in found], [2])


class FakeGh:
    def __init__(self, fail_delete: bool = False) -> None:
        self.calls: list[list[str]] = []
        self.fail_delete = fail_delete

    def __call__(self, args: list[str]) -> str:
        self.calls.append(args)
        path = next(a for a in args if a.startswith("repos/"))
        if "-X" in args:
            if self.fail_delete:
                raise subprocess.CalledProcessError(1, args, stderr="403")
            return ""
        if path.endswith("/branches?per_page=100"):
            return "\n".join(json.dumps(b) for b in [branch("main", "m"), branch("feat", "a1"), branch("wip", "w")])
        if "/pulls?" in path:
            return json.dumps(pr(7, "feat", "a1"))
        return "main\n"


class MainTest(unittest.TestCase):
    def run_main(self, fake: FakeGh, *extra: str) -> tuple[int, str, str]:
        with tempfile.TemporaryDirectory() as d:
            summary = Path(d) / "summary.md"
            out = io.StringIO()
            with redirect_stdout(out):
                rc = main(["--repo", REPO, "--summary-file", str(summary), *extra], run=fake)
            return rc, out.getvalue(), summary.read_text()

    def test_dry_run_deletes_nothing(self) -> None:
        fake = FakeGh()
        rc, out, summary = self.run_main(fake)
        self.assertEqual(rc, 0)
        self.assertFalse(any("-X" in c for c in fake.calls))
        self.assertIn("branch.candidate", out)
        self.assertIn("Would delete 1", summary)
        self.assertIn("pull/7", summary)

    def test_delete_only_candidates(self) -> None:
        fake = FakeGh()
        rc, out, summary = self.run_main(fake, "--dry-run", "false")
        self.assertEqual(rc, 0)
        deletes = [c for c in fake.calls if "-X" in c]
        self.assertEqual(deletes, [["api", "-X", "DELETE", f"repos/{REPO}/git/refs/heads/feat"]])
        self.assertIn("Deleted 1", summary)

    def test_failed_delete_exits_nonzero(self) -> None:
        rc, out, _ = self.run_main(FakeGh(fail_delete=True), "--dry-run", "false")
        self.assertEqual(rc, 1)
        self.assertIn("branch.delete_failed", out)


if __name__ == "__main__":
    unittest.main()
