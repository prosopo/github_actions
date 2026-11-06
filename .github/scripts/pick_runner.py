"""Choose the runs-on labels for every job of the calling workflow.

runs-on is evaluated before any step runs, so a job cannot pick its own runner;
pick-runner.yml runs this in a tiny hosted job and the caller's other jobs read
its `labels` output. Precedence, first match wins:

  1. fork PR                    -> hosted, always (fork code never reaches our VMs)
  2. `runner` input             -> hosted | self-hosted (auto falls through)
  3. `runner: <kind>` PR line   -> read from the PR body via the API at run time,
                                   so an edit applies on the next push or re-run
  4. GH_RUNNER_MAP[<workflow>]  -> repo var, JSON object keyed by workflow file
                                   stem, e.g. {"tests": "self-hosted"}
  5. hosted (ubuntu-latest)

Invalid GH_RUNNER_MAP JSON or an unknown kind anywhere falls back to hosted with
a warning rather than failing CI. Size labels per job (`sizes`) are appended to
the self-hosted base labels (vars.GH_RUNNER) only; hosted runners have no sizes.
The decision and its reason go to the job log and the run summary.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable

HOSTED = "hosted"
SELF_HOSTED = "self-hosted"
KINDS = {HOSTED, SELF_HOSTED}
HOSTED_LABELS = ["ubuntu-latest"]
DEFAULT_SELF_HOSTED_LABELS = ["self-hosted", "linux", "x64", "ubuntu-24.04"]
PR_LINE = re.compile(r"^\s*runner:\s*(\S+)\s*$", re.IGNORECASE | re.MULTILINE)

Fetch = Callable[[str, int], str]


@dataclass
class Decision:
    kind: str
    reason: str
    warnings: list[str]


def log(event: str, **fields: object) -> None:
    print(json.dumps({"event": event, **fields}, default=str), flush=True)


def workflow_key(workflow_ref: str) -> str:
    """`owner/repo/.github/workflows/tests.yml@refs/...` -> `tests`."""
    path = workflow_ref.split("@", 1)[0]
    name = path.rsplit("/", 1)[-1]
    return re.sub(r"\.ya?ml$", "", name)


def pr_line_kind(body: str | None) -> str | None:
    match = PR_LINE.search(body or "")
    return match.group(1).lower() if match else None


def decide(
    *,
    is_fork: bool,
    runner_input: str,
    pr_body: str | None,
    runner_map: str,
    workflow: str,
) -> Decision:
    warnings: list[str] = []
    if is_fork:
        return Decision(HOSTED, "fork PR: always hosted", warnings)

    requested = (runner_input or "auto").strip().lower()
    if requested in KINDS:
        return Decision(requested, f"runner input '{requested}'", warnings)
    if requested != "auto":
        warnings.append(f"runner input '{runner_input}' is not auto|hosted|self-hosted; ignored")

    line = pr_line_kind(pr_body)
    if line in KINDS:
        return Decision(line, f"'runner: {line}' line in the PR description", warnings)
    if line is not None:
        warnings.append(f"PR line 'runner: {line}' is not hosted|self-hosted; ignored")

    if runner_map.strip():
        try:
            parsed = json.loads(runner_map)
            if not isinstance(parsed, dict):
                raise ValueError("not a JSON object")
        except ValueError as err:
            warnings.append(f"GH_RUNNER_MAP is not a valid JSON object ({err}); using hosted")
            return Decision(HOSTED, "default (GH_RUNNER_MAP invalid)", warnings)
        entry = parsed.get(workflow)
        if isinstance(entry, str) and entry.lower() in KINDS:
            return Decision(entry.lower(), f"GH_RUNNER_MAP['{workflow}'] = '{entry}'", warnings)
        if entry is not None:
            warnings.append(f"GH_RUNNER_MAP['{workflow}'] = {entry!r} is not hosted|self-hosted; using hosted")

    return Decision(HOSTED, f"default: no override for '{workflow}'", warnings)


def base_self_hosted(gh_runner: str, warnings: list[str]) -> list[str]:
    if not gh_runner.strip():
        return list(DEFAULT_SELF_HOSTED_LABELS)
    try:
        parsed = json.loads(gh_runner)
    except ValueError:
        parsed = None
    if isinstance(parsed, list) and parsed and all(isinstance(x, str) for x in parsed):
        return parsed
    warnings.append("GH_RUNNER is not a JSON list of labels; using the default self-hosted labels")
    return list(DEFAULT_SELF_HOSTED_LABELS)


def parse_sizes(sizes: str, warnings: list[str]) -> dict[str, list[str]]:
    if not sizes.strip():
        return {}
    try:
        parsed = json.loads(sizes)
    except ValueError:
        parsed = None
    if not isinstance(parsed, dict):
        warnings.append("sizes is not a JSON object; no size labels applied")
        return {}
    out: dict[str, list[str]] = {}
    for job, value in parsed.items():
        labels = [value] if isinstance(value, str) else value
        if isinstance(labels, list) and all(isinstance(x, str) for x in labels):
            out[job] = labels
        else:
            warnings.append(f"sizes['{job}'] is not a label or list of labels; ignored")
    return out


def labels_for(kind: str, base: list[str], sizes: dict[str, list[str]]) -> tuple[list[str], dict[str, list[str]]]:
    """Returns (default labels, per-job labels)."""
    if kind == HOSTED:
        return list(HOSTED_LABELS), {job: list(HOSTED_LABELS) for job in sizes}
    return list(base), {job: base + [s for s in extra if s not in base] for job, extra in sizes.items()}


def fetch_pr_body(repo: str, number: int) -> str:
    return subprocess.run(
        ["gh", "api", f"repos/{repo}/pulls/{number}", "--jq", ".body // \"\""],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    ).stdout


def read_event(path: str) -> dict:
    if not path or not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def summary(decision: Decision, workflow: str, default: list[str], jobs: dict[str, list[str]]) -> str:
    rows = "\n".join(f"| `{job}` | `{json.dumps(labels)}` |" for job, labels in sorted(jobs.items()))
    lines = [
        f"### Runner: {decision.kind}",
        "",
        f"- Workflow key: `{workflow}`",
        f"- Reason: {decision.reason}",
        f"- Default labels: `{json.dumps(default)}`",
    ]
    lines += [f"- Warning: {w}" for w in decision.warnings]
    if rows:
        lines += ["", "| job | runs-on |", "| --- | --- |", rows]
    return "\n".join(lines) + "\n"


def run(env: dict[str, str], args: argparse.Namespace, fetch: Fetch = fetch_pr_body) -> dict[str, str]:
    event = read_event(env.get("GITHUB_EVENT_PATH", ""))
    pr = event.get("pull_request") or {}
    repo = env.get("GITHUB_REPOSITORY", "")
    head_repo = ((pr.get("head") or {}).get("repo") or {}).get("full_name")
    is_fork = bool(pr) and head_repo != repo
    workflow = args.workflow or workflow_key(env.get("GITHUB_WORKFLOW_REF", ""))

    pr_body: str | None = None
    warnings: list[str] = []
    if pr and not is_fork:
        try:
            pr_body = fetch(repo, int(pr["number"]))
        except (subprocess.SubprocessError, OSError, KeyError, ValueError) as err:
            warnings.append(f"could not read the PR description ({err}); PR line ignored")
            pr_body = pr.get("body")

    decision = decide(
        is_fork=is_fork,
        runner_input=args.runner,
        pr_body=pr_body,
        runner_map=env.get("GH_RUNNER_MAP", ""),
        workflow=workflow,
    )
    decision.warnings[:0] = warnings
    # The prosopo org sets GH_RUNNER to "ubuntu-24.04" and repos override it,
    # so only parse it when it is actually used.
    base = base_self_hosted(env.get("GH_RUNNER", ""), decision.warnings) if decision.kind == SELF_HOSTED else []
    sizes = parse_sizes(args.sizes, decision.warnings)
    default, jobs = labels_for(decision.kind, base, sizes)
    log("pick_runner.decision", workflow=workflow, kind=decision.kind, reason=decision.reason,
        default=default, jobs=jobs, warnings=decision.warnings)
    for w in decision.warnings:
        print(f"::warning title=pick-runner::{w}", flush=True)
    return {
        "kind": decision.kind,
        "reason": decision.reason,
        "default": json.dumps(default),
        "labels": json.dumps(jobs),
        "summary": summary(decision, workflow, default, jobs),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runner", default="auto", help="auto | hosted | self-hosted")
    parser.add_argument("--sizes", default="", help='JSON object job -> size label(s), e.g. {"build":["xlarge"]}')
    parser.add_argument("--workflow", default="", help="GH_RUNNER_MAP key; defaults to the caller's file stem")
    args = parser.parse_args(argv)
    env = dict(os.environ)
    result = run(env, args)
    if out := env.get("GITHUB_OUTPUT"):
        with open(out, "a", encoding="utf-8") as fh:
            for key in ("kind", "reason", "default", "labels"):
                fh.write(f"{key}={result[key]}\n")
    if path := env.get("GITHUB_STEP_SUMMARY"):
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(result["summary"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
