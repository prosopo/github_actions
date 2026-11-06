"""Delete remote branches whose PR merged and that nobody has pushed to since.

Most prosopo PRs squash-merge, so the branch tip is never an ancestor of the
default branch and `git branch --merged` cannot find them. The PR API can: a
branch is safe to delete when a merged PR from this repo has
head.ref == branch and head.sha == the branch's current tip, i.e. the branch
is exactly what was merged and nothing was committed after.

Kept regardless:
  - the default branch, protected branches and anything matching --keep
    (fnmatch patterns, default `release/*`);
  - a branch that is the head of an open PR from this repo, or the base of any
    open PR (deleting a base closes the PRs stacked on it);
  - a branch whose tip moved between listing and deleting.
PRs from forks never count: their head.ref names a branch in the fork, not here.

--dry-run (the default) only reports. Each deletion is logged with the PR link
and the sha, so a branch can be restored with
`gh api repos/<repo>/git/refs -f ref=refs/heads/<branch> -f sha=<sha>`.
The candidate table goes to $GITHUB_STEP_SUMMARY when set.
"""

import argparse
import fnmatch
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable
from urllib.parse import quote

Api = Callable[[list[str]], str]


def log(event: str, **fields: object) -> None:
    print(json.dumps({"event": event, **fields}, default=str), flush=True)


def gh(args: list[str]) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True, timeout=120).stdout


@dataclass(frozen=True)
class Branch:
    name: str
    sha: str
    protected: bool


@dataclass(frozen=True)
class Candidate:
    branch: str
    sha: str
    pr_number: int
    pr_url: str
    merged_at: str


class Cleaner:
    def __init__(self, repo: str, api: Api = gh, keep: tuple[str, ...] = ("release/*",)):
        self.repo, self.api, self.keep = repo, api, keep
        self.owner = repo.split("/")[0]

    def _json(self, path: str, paginate: bool = False) -> list | dict:
        args = ["api", path]
        if paginate:
            # --slurp wraps each page in an outer array, so pages are flattened below.
            args += ["--paginate", "--slurp"]
        out = json.loads(self.api(args) or "null")
        if paginate:
            return [item for page in out for item in page]
        return out

    def default_branch(self) -> str:
        return self._json(f"repos/{self.repo}")["default_branch"]

    def branches(self) -> list[Branch]:
        return [Branch(b["name"], b["commit"]["sha"], bool(b.get("protected")))
                for b in self._json(f"repos/{self.repo}/branches?per_page=100", paginate=True)]

    def _pulls(self, query: str) -> list[dict]:
        return self._json(f"repos/{self.repo}/pulls?{query}&per_page=100", paginate=True)

    def skip_reason(self, branch: Branch, default: str) -> str | None:
        if branch.name == default:
            return "default"
        if branch.protected:
            return "protected"
        if any(fnmatch.fnmatchcase(branch.name, p) for p in self.keep):
            return "keep-pattern"
        return None

    def _is_own(self, p: dict) -> bool:
        return (p["head"].get("repo") or {}).get("full_name") == self.repo

    def open_refs(self) -> tuple[set[str], set[str]]:
        """Heads of this repo's open PRs and bases of all open PRs, fetched once rather than per branch."""
        opened = self._pulls("state=open")
        return {p["head"]["ref"] for p in opened if self._is_own(p)}, {p["base"]["ref"] for p in opened}

    def evaluate(self, branch: Branch, open_heads: set[str], open_bases: set[str]) -> tuple[Candidate | None, str]:
        if branch.name in open_heads:
            return None, "open-pr"
        if branch.name in open_bases:
            return None, "base-of-open-pr"
        ref = quote(branch.name, safe="")
        own = [p for p in self._pulls(f"state=closed&head={self.owner}:{ref}")
               if p["head"]["ref"] == branch.name and self._is_own(p)]
        merged = [p for p in own if p.get("merged_at")]
        if not merged:
            return None, "not-merged" if own else "no-pr"
        exact = [p for p in merged if p["head"]["sha"] == branch.sha]
        if not exact:
            return None, "commits-after-merge"
        pr = max(exact, key=lambda p: p["merged_at"])
        return Candidate(branch.name, branch.sha, pr["number"], pr["html_url"], pr["merged_at"]), "candidate"

    def candidates(self) -> list[Candidate]:
        default = self.default_branch()
        open_heads, open_bases = self.open_refs()
        found: list[Candidate] = []
        for b in self.branches():
            reason = self.skip_reason(b, default)
            cand = None
            if reason is None:
                cand, reason = self.evaluate(b, open_heads, open_bases)
            log("branch", name=b.name, sha=b.sha, result=reason)
            if cand:
                found.append(cand)
        return found

    def delete(self, cand: Candidate) -> bool:
        ref = quote(cand.branch, safe="")
        tip = self._json(f"repos/{self.repo}/branches/{ref}")["commit"]["sha"]
        if tip != cand.sha:
            log("skip-moved", branch=cand.branch, expected=cand.sha, actual=tip)
            return False
        self.api(["api", "-X", "DELETE", f"repos/{self.repo}/git/refs/heads/{ref}"])
        log("deleted", branch=cand.branch, sha=cand.sha, pr=cand.pr_url)
        return True


def summary(repo: str, cands: list[Candidate], dry_run: bool, deleted: set[str]) -> str:
    verb = "Would delete" if dry_run else "Deleted"
    lines = [f"## Branch cleanup: {repo} ({'dry run' if dry_run else 'deleting'})", "",
             f"{verb} {len(deleted) if not dry_run else len(cands)} of {len(cands)} candidate(s).", ""]
    if cands:
        lines += ["| branch | sha | merged PR | merged at | result |", "|---|---|---|---|---|"]
        for c in cands:
            result = "dry run" if dry_run else ("deleted" if c.branch in deleted else "kept (tip moved)")
            lines.append(f"| `{c.branch}` | `{c.sha}` | [#{c.pr_number}]({c.pr_url}) | {c.merged_at} | {result} |")
        lines += ["", f"Restore one: `gh api repos/{repo}/git/refs -f ref=refs/heads/<branch> -f sha=<sha>`"]
    return "\n".join(lines) + "\n"


def run(repo: str, dry_run: bool, keep: tuple[str, ...], api: Api = gh, summary_path: str | None = None) -> int:
    cleaner = Cleaner(repo, api, keep)
    cands = cleaner.candidates()
    deleted: set[str] = set()
    if not dry_run:
        deleted = {c.branch for c in cands if cleaner.delete(c)}
    text = summary(repo, cands, dry_run, deleted)
    print(text)
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write(text)
    log("done", repo=repo, dry_run=dry_run, candidates=len(cands), deleted=len(deleted))
    return 0


def parse_bool(v: str) -> bool:
    if v.lower() in ("true", "1", "yes"):
        return True
    if v.lower() in ("false", "0", "no"):
        return False
    raise argparse.ArgumentTypeError(f"expected true/false, got {v!r}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", required=True, help="owner/name")
    ap.add_argument("--dry-run", type=parse_bool, default=True, help="true (default) only reports")
    ap.add_argument("--keep", default="release/*", help="comma-separated fnmatch patterns never deleted")
    a = ap.parse_args(argv)
    keep = tuple(p.strip() for p in a.keep.split(",") if p.strip())
    return run(a.repo, a.dry_run, keep, summary_path=os.environ.get("GITHUB_STEP_SUMMARY"))


if __name__ == "__main__":
    sys.exit(main())
