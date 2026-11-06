"""Keep one auto-merge PR at a time moving towards merge by updating its branch.

Repos that require branches to be up to date make every merge leave the other
PRs BEHIND. Updating them all at once wastes a CI run on each when the first
merges; updating none leaves them stuck. So this updates exactly one, chosen at
random among the eligible (random rather than oldest-first so one PR whose CI
keeps failing cannot starve the rest), and stands down while any non-draft PR
has CI running so it never competes with work already in flight.

The PR it updated carries LABEL, which is how later runs recognise it:
  - its CI failed -> drop the label and pick another PR now;
  - main moved while its CI was still running -> that run is already stale, so
    cancel it and update the branch again immediately rather than waiting.

Eligible: open against base, non-draft, auto-merge on, mergeable, BEHIND, no
unresolved review threads. Design: https://github.com/prosopo/github_actions
"""

import argparse
import json
import random
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

RUNNING = {"QUEUED", "IN_PROGRESS", "PENDING", "WAITING", "REQUESTED"}
FAILED = {"FAILURE", "ERROR", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE"}

Runner = Callable[[list[str]], str]


def log(event: str, **fields: object) -> None:
    print(json.dumps({"event": event, **fields}, default=str), flush=True)


def gh(args: list[str]) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True, timeout=120).stdout


def default_branch(repo: str) -> str:
    return gh(["api", f"repos/{repo}", "--jq", ".default_branch"]).strip()


@dataclass
class PR:
    number: int
    draft: bool
    auto_merge: bool
    labels: set[str]
    head_sha: str
    checks: list[dict] = field(default_factory=list)


def age_minutes(ts: str | None, now: datetime) -> float:
    if not ts:
        return 0.0
    return (now - datetime.fromisoformat(ts.replace("Z", "+00:00"))).total_seconds() / 60


def ci_running(pr: PR, now: datetime, max_age: float) -> bool:
    """A check pending for longer than max_age is hung, not busy, so it cannot gate forever."""
    for c in pr.checks:
        state = c.get("status") or c.get("state") or ""
        if state in RUNNING and age_minutes(c.get("startedAt") or c.get("createdAt"), now) <= max_age:
            return True
    return False


def ci_failed(pr: PR) -> bool:
    return any((c.get("conclusion") or c.get("state") or "") in FAILED for c in pr.checks)


class Updater:
    def __init__(self, repo: str, base: str, label: str, run: Runner = gh,
                 sleep: Callable[[float], None] = time.sleep, rng: random.Random | None = None,
                 max_age: float = 90, poll_attempts: int = 6, poll_seconds: float = 10):
        self.repo, self.base, self.label = repo, base, label
        self.run, self.sleep, self.rng = run, sleep, rng or random.Random()
        self.max_age, self.poll_attempts, self.poll_seconds = max_age, poll_attempts, poll_seconds

    def list_prs(self) -> list[PR]:
        raw = json.loads(self.run([
            "pr", "list", "--repo", self.repo, "--state", "open", "--base", self.base, "--limit", "100",
            "--json", "number,isDraft,autoMergeRequest,labels,headRefOid,statusCheckRollup"]))
        return [PR(p["number"], p["isDraft"], p["autoMergeRequest"] is not None,
                   {lbl["name"] for lbl in p["labels"]}, p["headRefOid"], p.get("statusCheckRollup") or [])
                for p in raw]

    def merge_state(self, n: int) -> tuple[str, str]:
        for _ in range(self.poll_attempts):
            out = json.loads(self.run(["pr", "view", str(n), "--repo", self.repo, "--json", "mergeable,mergeStateStatus"]))
            if "UNKNOWN" not in (out["mergeable"], out["mergeStateStatus"]):
                return out["mergeable"], out["mergeStateStatus"]
            self.sleep(self.poll_seconds)
        return "UNKNOWN", "UNKNOWN"

    def unresolved_threads(self, n: int) -> int:
        owner, name = self.repo.split("/")
        out = self.run([
            "api", "graphql", "-f", f"owner={owner}", "-f", f"name={name}", "-F", f"number={n}", "-f",
            "query=query($owner:String!,$name:String!,$number:Int!){repository(owner:$owner,name:$name)"
            "{pullRequest(number:$number){reviewThreads(first:100){nodes{isResolved}}}}}",
            "--jq", "[.data.repository.pullRequest.reviewThreads.nodes[] | select(.isResolved == false)] | length"])
        return int(out.strip() or 0)

    def eligible(self, pr: PR) -> bool:
        if pr.draft or not pr.auto_merge:
            return False
        mergeable, state = self.merge_state(pr.number)
        if mergeable != "MERGEABLE" or state != "BEHIND":
            log("auto_update.skip", pr=pr.number, mergeable=mergeable, state=state)
            return False
        threads = self.unresolved_threads(pr.number)
        if threads:
            log("auto_update.skip", pr=pr.number, unresolved_threads=threads)
            return False
        return True

    def ensure_label(self) -> None:
        self.run(["label", "create", self.label, "--repo", self.repo, "--force", "--color", "ededed",
                  "--description", "Branch kept up to date by auto-update-pr"])

    def set_label(self, n: int, on: bool) -> None:
        flag = "--add-label" if on else "--remove-label"
        self.run(["pr", "edit", str(n), "--repo", self.repo, flag, self.label])

    def cancel_runs(self, pr: PR) -> None:
        runs = json.loads(self.run(["api", f"repos/{self.repo}/actions/runs?head_sha={pr.head_sha}&per_page=100"]))
        for r in runs.get("workflow_runs", []):
            if r["status"] in ("queued", "in_progress", "waiting", "pending", "requested"):
                try:
                    self.run(["api", "-X", "POST", f"repos/{self.repo}/actions/runs/{r['id']}/cancel"])
                except subprocess.CalledProcessError:
                    log("auto_update.cancel_failed", run=r["id"])

    def update(self, pr: PR) -> None:
        self.run(["api", "-X", "PUT", f"repos/{self.repo}/pulls/{pr.number}/update-branch"])
        log("auto_update.updated", pr=pr.number, url=f"https://github.com/{self.repo}/pull/{pr.number}")

    def step(self, event: str, now: datetime) -> int | None:
        """One pass. Returns the PR number updated, if any."""
        self.ensure_label()
        prs = self.list_prs()
        ours = [p for p in prs if self.label in p.labels]
        for p in ours:
            if p.draft or not p.auto_merge or (ci_failed(p) and not ci_running(p, now, self.max_age)):
                log("auto_update.release", pr=p.number, failed=ci_failed(p))
                self.set_label(p.number, False)
                p.labels.discard(self.label)
            elif event == "push" and ci_running(p, now, self.max_age):
                log("auto_update.restart", pr=p.number, reason="base moved while its CI ran")
                self.cancel_runs(p)
                self.update(p)
                return p.number

        busy = [p.number for p in prs if not p.draft and ci_running(p, now, self.max_age)]
        if busy:
            log("auto_update.stand_down", busy=busy)
            return None

        candidates = [p for p in prs if not p.draft and p.auto_merge]
        self.rng.shuffle(candidates)
        for p in candidates:
            if self.eligible(p):
                for other in ours:
                    if self.label in other.labels and other.number != p.number:
                        self.set_label(other.number, False)
                self.update(p)
                if self.label not in p.labels:
                    self.set_label(p.number, True)
                return p.number
        log("auto_update.nothing_eligible", considered=len(candidates))
        return None


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--repo", required=True)
    p.add_argument("--base", default="", help="target branch; empty means the repo's default branch")
    p.add_argument("--label", default="auto-update")
    p.add_argument("--event", default="schedule")
    p.add_argument("--max-age-minutes", type=float, default=90)
    a = p.parse_args(argv)
    base = a.base or default_branch(a.repo)
    Updater(a.repo, base, a.label, max_age=a.max_age_minutes).step(a.event, datetime.now(timezone.utc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
