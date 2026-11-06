"""Delete branches whose work is merged and that nobody has pushed to since.

A branch is deleted only when a merged PR in this repo had it as head AND that
PR's head sha is still the branch tip: a push after the merge means someone is
still using it, so it stays. Never touched: the default branch, protected
branches, release/*, and any branch an open PR uses. Fork PRs are ignored, since
their head ref names a branch in the fork, not here.

--dry-run (the default in the workflow) only lists candidates, so a repo can see
what would go before turning deletion on. Each deletion logs its PR and sha, so
`git push origin <sha>:refs/heads/<branch>` restores one.
"""

import argparse
import json
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass

KEEP_PREFIXES = ("release/",)

Runner = Callable[[list[str]], str]


def log(event: str, **fields: object) -> None:
    print(json.dumps({"event": event, **fields}, default=str), flush=True)


def gh(args: list[str]) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True, timeout=600).stdout


def json_lines(out: str) -> list[dict]:
    return [json.loads(line) for line in out.splitlines() if line.strip()]


@dataclass
class Candidate:
    branch: str
    sha: str
    pr: int
    url: str


def candidates(repo: str, default: str, branches: list[dict], prs: list[dict]) -> list[Candidate]:
    in_repo = [p for p in prs if p.get("head_repo") == repo]
    open_heads = {p["head_ref"] for p in in_repo if p["state"] == "open"}
    merged = {}
    for p in in_repo:
        if p.get("merged_at"):
            merged.setdefault(p["head_ref"], []).append(p)
    out = []
    for b in branches:
        name = b["name"]
        if name == default or b.get("protected") or name.startswith(KEEP_PREFIXES) or name in open_heads:
            continue
        match = next((p for p in merged.get(name, []) if p["head_sha"] == b["sha"]), None)
        if match:
            out.append(Candidate(name, b["sha"], match["number"], match["url"]))
    return sorted(out, key=lambda c: c.branch)


def fetch(repo: str, run: Runner) -> tuple[str, list[dict], list[dict]]:
    default = run(["api", f"repos/{repo}", "--jq", ".default_branch"]).strip()
    branches = json_lines(run(["api", "--paginate", f"repos/{repo}/branches?per_page=100",
                               "--jq", ".[] | {name, sha: .commit.sha, protected}"]))
    prs = json_lines(run(["api", "--paginate", f"repos/{repo}/pulls?state=all&per_page=100",
                          "--jq", ".[] | {number, state, merged_at, url: .html_url, head_ref: .head.ref,"
                          " head_sha: .head.sha, head_repo: .head.repo.full_name}"]))
    return default, branches, prs


def summary(found: list[Candidate], dry_run: bool) -> str:
    verb = "Would delete" if dry_run else "Deleted"
    lines = [f"### {verb} {len(found)} merged branch(es)", ""]
    lines += [f"- `{c.branch}` at `{c.sha[:12]}` — merged in {c.url}" for c in found]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None, run: Runner = gh) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--repo", required=True)
    p.add_argument("--dry-run", choices=["true", "false"], default="true")
    p.add_argument("--summary-file", default="")
    a = p.parse_args(argv)
    dry_run = a.dry_run == "true"
    found = candidates(a.repo, *fetch(a.repo, run))
    failed = 0
    for c in found:
        if dry_run:
            log("branch.candidate", branch=c.branch, sha=c.sha, pr=c.url)
            continue
        try:
            run(["api", "-X", "DELETE", f"repos/{a.repo}/git/refs/heads/{c.branch}"])
            log("branch.deleted", branch=c.branch, sha=c.sha, pr=c.url)
        except subprocess.CalledProcessError as e:
            failed += 1
            log("branch.delete_failed", branch=c.branch, error=(e.stderr or "").strip())
    log("branch_cleanup.done", repo=a.repo, dry_run=dry_run, candidates=len(found), failed=failed)
    if a.summary_file:
        with open(a.summary_file, "a") as f:
            f.write(summary(found, dry_run))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
