"""Fail when a workflow breaks a mechanically checkable prosopo workflow policy.

actionlint covers syntax and expressions; this covers house rules, so drift
cannot come back after each cleanup. Rules (ids usable in waivers):

  concurrency      workflow declares concurrency; a workflow_call file takes its
                   group from a concurrency-key input, never github.workflow
                   (inside a called workflow that names the caller)
  shell            defaults.run.shell is set at workflow level
  pr-types         pull_request lists opened, synchronize, reopened,
                   ready_for_review and converted_to_draft, and each root job
                   (no needs:) skips drafts
  pr-target        every pull_request_target job has a fork guard
  permissions      top-level permissions exist and grant no write; no job is
                   write-all
  allow-dirty      cargo is not run with --allow-dirty
  literal-main     no hardcoded 'main' ref outside the on: block
  secret-to-file   a secret is not redirected into a file or GITHUB_ENV

A finding is waived by a comment on its line or the line above:
  # workflow-lint: allow <rule> -- <reason>
A waiver without a reason is itself a finding.
"""

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

PR_TYPES = {"opened", "synchronize", "reopened", "ready_for_review", "converted_to_draft"}
WAIVER_RE = re.compile(r"#\s*workflow-lint:\s*allow\s+([a-z-]+)\s*(?:--\s*(.*\S))?")
LITERAL_MAIN_RE = re.compile(r"refs/heads/main\b|origin/main\b|['\"]main['\"]")
SECRET_TO_FILE_RE = re.compile(r"secrets\.[A-Za-z0-9_]+.*(?:>|GITHUB_ENV)")
ALLOW_DIRTY_RE = re.compile(r"\bcargo\b.*--allow-dirty")


@dataclass(frozen=True)
class Finding:
    path: Path
    line: int
    rule: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: [{self.rule}] {self.message}"


class LineLoader(yaml.SafeLoader):
    """SafeLoader that records each mapping's line as `__line__`."""


def _construct_mapping(loader: LineLoader, node: yaml.MappingNode) -> dict:
    mapping = loader.construct_mapping(node, deep=True)
    mapping["__line__"] = node.start_mark.line + 1
    return mapping


LineLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


def _triggers(doc: dict) -> dict:
    # YAML 1.1 reads a bare `on` key as boolean True.
    on = doc.get("on", doc.get(True))
    if isinstance(on, str):
        return {on: {}}
    if isinstance(on, list):
        return {t: {} for t in on}
    return on if isinstance(on, dict) else {}


def _jobs(doc: dict) -> dict:
    jobs = doc.get("jobs")
    if not isinstance(jobs, dict):
        return {}
    return {k: v for k, v in jobs.items() if k != "__line__" and isinstance(v, dict)}


def _on_block(lines: list[str]) -> range:
    start = next((i for i, l in enumerate(lines) if re.match(r"^['\"]?on['\"]?:", l)), None)
    if start is None:
        return range(0)
    end = next((i for i in range(start + 1, len(lines)) if re.match(r"^[A-Za-z_'\"]", lines[i])), len(lines))
    return range(start, end)


def _structural(path: Path, doc: dict) -> list[Finding]:
    found = []
    top = doc.get("__line__", 1)
    triggers = _triggers(doc)
    jobs = _jobs(doc)

    concurrency = doc.get("concurrency")
    if concurrency is None:
        found.append(Finding(path, top, "concurrency", "workflow has no concurrency"))
    elif "workflow_call" in triggers:
        group = concurrency.get("group", "") if isinstance(concurrency, dict) else str(concurrency)
        line = concurrency.get("__line__", top) if isinstance(concurrency, dict) else top
        if "github.workflow" in group:
            found.append(Finding(path, line, "concurrency", "workflow_call group uses github.workflow, which names the caller"))
        if "inputs.concurrency-key" not in group:
            found.append(Finding(path, line, "concurrency", "workflow_call group does not use inputs.concurrency-key"))

    defaults = doc.get("defaults")
    run = defaults.get("run") if isinstance(defaults, dict) else None
    if not (isinstance(run, dict) and run.get("shell")):
        found.append(Finding(path, top, "shell", "no defaults.run.shell at workflow level"))

    if "pull_request" in triggers:
        pr = triggers["pull_request"] or {}
        missing = PR_TYPES - set(pr.get("types") or [])
        if missing:
            found.append(Finding(path, pr.get("__line__", top), "pr-types", f"pull_request types missing {', '.join(sorted(missing))}"))
        for job_id, job in jobs.items():
            if not job.get("needs") and "pull_request.draft" not in str(job.get("if", "")):
                found.append(Finding(path, job["__line__"], "pr-types", f"job '{job_id}' does not skip draft PRs"))

    if "pull_request_target" in triggers:
        for job_id, job in jobs.items():
            if "head.repo" not in str(job.get("if", "")):
                found.append(Finding(path, job["__line__"], "pr-target", f"job '{job_id}' has no fork guard on pull_request_target"))

    perms = doc.get("permissions")
    if perms is None:
        found.append(Finding(path, top, "permissions", "no top-level permissions"))
    elif perms == "write-all" or (isinstance(perms, dict) and "write" in perms.values()):
        line = perms.get("__line__", top) if isinstance(perms, dict) else top
        found.append(Finding(path, line, "permissions", "top-level permissions grant write; grant it on the job that needs it"))
    for job_id, job in jobs.items():
        if job.get("permissions") == "write-all":
            found.append(Finding(path, job["__line__"], "permissions", f"job '{job_id}' is write-all"))
    return found


def _textual(path: Path, lines: list[str]) -> list[Finding]:
    found = []
    on = _on_block(lines)
    for i, line in enumerate(lines):
        code = line.split(" #", 1)[0] if not line.lstrip().startswith("#") else ""
        n = i + 1
        if ALLOW_DIRTY_RE.search(code):
            found.append(Finding(path, n, "allow-dirty", "cargo --allow-dirty"))
        if i not in on and LITERAL_MAIN_RE.search(code):
            found.append(Finding(path, n, "literal-main", "hardcoded main; use github.event.repository.default_branch"))
        if SECRET_TO_FILE_RE.search(code):
            found.append(Finding(path, n, "secret-to-file", "secret written to a file or GITHUB_ENV; pass it via env"))
    return found


def _waivers(path: Path, lines: list[str]) -> tuple[dict[int, set[str]], list[Finding]]:
    waived: dict[int, set[str]] = {}
    bad = []
    for i, line in enumerate(lines):
        m = WAIVER_RE.search(line)
        if not m:
            continue
        if not m.group(2):
            bad.append(Finding(path, i + 1, "waiver", f"waiver for {m.group(1)} has no reason (`-- <why>`)"))
            continue
        for n in (i + 1, i + 2):
            waived.setdefault(n, set()).add(m.group(1))
    return waived, bad


def problems(path: Path) -> list[Finding]:
    text = path.read_text()
    lines = text.splitlines()
    doc = yaml.load(text, Loader=LineLoader) or {}
    if not isinstance(doc, dict):
        return []
    waived, bad = _waivers(path, lines)
    found = _structural(path, doc) + _textual(path, lines)
    return bad + [f for f in found if f.rule not in waived.get(f.line, set())]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--dir", default=".github/workflows")
    a = p.parse_args(argv)
    files = sorted(Path(a.dir).glob("*.yml")) + sorted(Path(a.dir).glob("*.yaml"))
    found = [f for path in files for f in problems(path)]
    for f in found:
        print(f"::error file={f.path},line={f.line}::[{f.rule}] {f.message}")
    print(f"checked {len(files)} workflow files, {len(found)} findings")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
