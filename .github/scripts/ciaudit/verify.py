"""Cross-check statically found steps against the latest green default-branch run.

For each (repo, workflow) the latest successful run on the default branch is
fetched (falling back to the latest successful run of any branch for
PR-only workflows, flagged as such), then the job and step that the static
analysis attributed a hit to are looked up and their conclusion recorded.
A step that was skipped, or a job absent from the run (filtered by `if:` or a
paths filter), does not count as proof.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from workflows import WILD, StepRef

Api = Callable[[str], object]


def gh_api_factory(cache_file: Path | None, timeout: int = 120) -> Api:
    cache: dict[str, object] = {}
    if cache_file and cache_file.exists():
        cache = json.loads(cache_file.read_text())

    def api(path: str) -> object:
        if path in cache:
            return cache[path]
        last = ""
        for _attempt in range(3):
            # per_page=1 run lookups must not paginate: that walks every run ever made.
            args = ["gh", "api", path] if "per_page=1" in path else ["gh", "api", "--paginate", path]
            proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
            if proc.returncode == 0:
                out = _concat_pages(proc.stdout)
                cache[path] = out
                if cache_file:
                    cache_file.write_text(json.dumps(cache))
                return out
            last = proc.stderr[:300]
            if "404" in last or "Not Found" in last:
                cache[path] = None
                return None
        raise RuntimeError(f"gh api {path}: {last}")

    return api


def _concat_pages(text: str) -> object:
    """--paginate prints one JSON document per page; merge list fields of object pages."""
    dec = json.JSONDecoder()
    i, docs = 0, []
    text = text.strip()
    while i < len(text):
        doc, j = dec.raw_decode(text, i)
        docs.append(doc)
        i = j
        while i < len(text) and text[i].isspace():
            i += 1
    if len(docs) == 1:
        return docs[0]
    if all(isinstance(d, list) for d in docs):
        return [x for d in docs for x in d]
    merged: dict[str, object] = {}
    for d in docs:
        for k, val in d.items():
            if isinstance(val, list):
                merged.setdefault(k, [])
                merged[k] += val  # type: ignore[operator]
            else:
                merged.setdefault(k, val)
    return merged


@dataclass
class Check:
    status: str  # success | skipped | failure | job-missing | step-missing | no-run
    run_url: str = ""
    job_url: str = ""
    source: str = ""  # "main" | "non-default-branch"
    detail: str = ""


def pattern(display: str) -> re.Pattern[str]:
    parts = [re.escape(p) for p in display.split(WILD)]
    return re.compile(".*".join(parts), re.S)


def job_matches(api_name: str, chain: tuple[str, ...]) -> bool:
    """GitHub names a called job `caller / called`, and appends `(matrix, values)`."""
    want = " / ".join(chain)
    rx = pattern(want)
    name = api_name
    for _ in range(len(chain) + 1):
        if rx.fullmatch(name) or rx.fullmatch(re.sub(r" \([^()]*\)$", "", name)):
            return True
        name = re.sub(r" \([^()]*\)( / |$)", r"\1", name, count=1)
    return bool(re.fullmatch(".*".join(re.escape(p) for p in want.split(WILD)) + r"( \(.*\))?", api_name))


class Verifier:
    def __init__(self, api: Api, owner: str, default_branch: dict[str, str]):
        self.api = api
        self.owner = owner
        self.default_branch = default_branch
        self._runs: dict[tuple[str, str], tuple[dict | None, str]] = {}
        self._jobs: dict[int, list[dict]] = {}

    def latest_run(self, repo: str, workflow: str) -> tuple[dict | None, str]:
        key = (repo, workflow)
        if key not in self._runs:
            br = self.default_branch.get(repo, "main")
            base = f"repos/{self.owner}/{repo}/actions/workflows/{workflow}/runs?status=success&per_page=1"
            got = self._first(f"{base}&branch={br}&exclude_pull_requests=true")
            src = "main"
            if got is None:
                got, src = self._first(base), "non-default-branch"
            self._runs[key] = (got, src)
        return self._runs[key]

    def _first(self, path: str) -> dict | None:
        try:
            r = self.api(path)
        except RuntimeError:
            return None
        runs = (r or {}).get("workflow_runs") if isinstance(r, dict) else None
        return runs[0] if runs else None

    def jobs(self, repo: str, run_id: int) -> list[dict]:
        if run_id not in self._jobs:
            r = self.api(f"repos/{self.owner}/{repo}/actions/runs/{run_id}/jobs?per_page=100")
            self._jobs[run_id] = list((r or {}).get("jobs") or []) if isinstance(r, dict) else []
        return self._jobs[run_id]

    def check(self, ref: StepRef) -> Check:
        c = self._check(ref, *self.latest_run(ref.repo, ref.workflow))
        if c.status in ("job-missing", "step-missing") and c.source == "main":
            # The newest green main run can predate the job (workflow later made PR-only or restructured).
            base = f"repos/{self.owner}/{ref.repo}/actions/workflows/{ref.workflow}/runs?status=success&per_page=1"
            alt = self._first(base)
            if alt is not None:
                c2 = self._check(ref, alt, "non-default-branch")
                if c2.status == "success":
                    return c2
        return c

    def _check(self, ref: StepRef, run: dict | None, src: str) -> Check:
        if run is None:
            return Check("no-run", detail=f"no successful run of {ref.workflow}")
        jobs = [j for j in self.jobs(ref.repo, run["id"]) if job_matches(j.get("name", ""), ref.job)]
        if not jobs:
            return Check("job-missing", run["html_url"], source=src, detail=" / ".join(ref.job).replace(WILD, "*"))
        srx = pattern(ref.step)
        best: Check | None = None
        for j in jobs:
            steps = j.get("steps") or []
            hit = [s for s in steps if srx.fullmatch(s.get("name", ""))]
            if not hit:
                offset = 2 + any(s.get("name") == "Initialize containers" for s in steps)
                hit = [s for s in steps if s.get("number") == ref.index + offset]
            if not hit:
                c = Check("step-missing", run["html_url"], j.get("html_url", ""), src, ref.step.replace(WILD, "*"))
            else:
                concl = hit[0].get("conclusion") or "unknown"
                c = Check("success" if concl == "success" else concl, run["html_url"], j.get("html_url", ""), src, hit[0].get("name", ""))
            if c.status == "success":
                return c
            best = best or c
        assert best is not None
        return best
