"""Fail when a workflow job or step has no timeout-minutes, or a hang-prone command runs unbounded.

GitHub's default is 360 minutes per job, and a hung step on a self-hosted
runner holds that VM for all of it. Jobs that call a reusable workflow
(`uses:` at job level) are exempt: GitHub rejects timeout-minutes there, and
the called workflow's own jobs are checked where they are defined.

Composite-action steps cannot set timeout-minutes, and a step timeout on a
multi-command block does not say which command hung, so network, install,
container and test commands in `run:` blocks (workflows and composite actions)
must start with `timeout N`, use the tool's own limit (curl/wget --max-time),
or carry a `# no-timeout: <why>` comment.
"""

import argparse
import re
import sys
from pathlib import Path

import yaml


class LineLoader(yaml.SafeLoader):
    """SafeLoader that records each mapping's line as `__line__`."""


def _construct_mapping(loader: LineLoader, node: yaml.MappingNode) -> dict:
    mapping = loader.construct_mapping(node, deep=True)
    mapping["__line__"] = node.start_mark.line + 1
    return mapping


LineLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


HANG_PRONE = re.compile(
    r"^(pnpm|npm|npx|yarn|docker|cargo|pip3?|apt-get|apt|gh|rustup|brew|helm|kubectl|ansible-playbook"
    r"|playwright|cypress|curl|wget|git (clone|fetch|pull|push|submodule))(?=\s|$)"
)
TOOL_LIMIT = re.compile(r"^(curl|wget)\b.*(--max-time|\s-m\s|--timeout)")
SEPARATORS = re.compile(r"&&|\|\||;|(?<!\|)\|(?!\|)")
PREFIXES = re.compile(r"^(if|elif|while|until|then|do|else|time|!|\(|\{|exec|sudo|env(\s+\w+=\S+)*)\s+")
ASSIGNMENT = re.compile(r"^(\w+=(\"[^\"]*\"|'[^']*'|\S*)\s+)+")
SUBSTITUTION = re.compile(r"^(\w+=)?[\"']?(\$\(|`)")


def unbounded_commands(script: str) -> list[tuple[int, str]]:
    """Return (line offset, command) for each hang-prone command with no time limit."""
    found = []
    heredoc = None
    continued = False
    for offset, raw in enumerate(script.splitlines()):
        line = raw.strip()
        if heredoc is not None:
            if line == heredoc:
                heredoc = None
            continue
        was_continued, continued = continued, line.endswith("\\")
        if was_continued or not line or line.startswith("#") or "# no-timeout" in line:
            continue
        marker = re.search(r"<<-?\s*['\"]?(\w+)", line)
        if marker:
            heredoc = marker.group(1)
        for segment in SEPARATORS.split(line.split(" #")[0]):
            cmd = segment.strip()
            while True:
                stripped = ASSIGNMENT.sub("", SUBSTITUTION.sub("", PREFIXES.sub("", cmd)))
                if stripped == cmd:
                    break
                cmd = stripped
            if HANG_PRONE.match(cmd) and not TOOL_LIMIT.match(cmd):
                found.append((offset, cmd.split()[0] + (" " + cmd.split()[1] if cmd.startswith("git ") else "")))
    return found


def _run_line(lines: list[str], step_line: int) -> int:
    """1-based line of the step's `run:` key, searching down from the step's first line."""
    for n in range(step_line - 1, len(lines)):
        if re.match(r"^\s*(- )?run:", lines[n]):
            return n + 1
    return step_line


def _step_problems(path: Path, lines: list[str], where: str, step: dict) -> list[str]:
    script = step.get("run")
    if not isinstance(script, str):
        return []
    run_line = _run_line(lines, step["__line__"])
    # A block scalar (`run: |`) starts its script on the next line; an inline one is on the key's line.
    first = run_line + 1 if re.search(r"run:\s*[|>]", lines[run_line - 1]) else run_line
    return [
        f"{path}:{first + offset}: {where} runs '{cmd}' without timeout (wrap in `timeout N` or add `# no-timeout: <why>`)"
        for offset, cmd in unbounded_commands(script)
    ]


def action_problems(path: Path) -> list[str]:
    text = path.read_text()
    doc = yaml.load(text, Loader=LineLoader) or {}
    runs = doc.get("runs") if isinstance(doc, dict) else None
    if not isinstance(runs, dict) or runs.get("using") != "composite":
        return []
    found = []
    for i, step in enumerate(runs.get("steps") or []):
        if isinstance(step, dict):
            label = step.get("name") or step.get("uses") or f"#{i + 1}"
            found += _step_problems(path, text.splitlines(), f"step '{label}'", step)
    return found


def problems(path: Path) -> list[str]:
    text = path.read_text()
    doc = yaml.load(text, Loader=LineLoader) or {}
    found = []
    jobs = doc.get("jobs") if isinstance(doc, dict) else None
    for job_id, job in (jobs or {}).items():
        if job_id == "__line__" or not isinstance(job, dict) or "uses" in job:
            continue
        if "timeout-minutes" not in job:
            found.append(f"{path}:{job['__line__']}: job '{job_id}' has no timeout-minutes")
        for i, step in enumerate(job.get("steps") or []):
            if isinstance(step, dict) and "timeout-minutes" not in step:
                label = step.get("name") or step.get("uses") or f"#{i + 1}"
                found.append(f"{path}:{step['__line__']}: job '{job_id}' step '{label}' has no timeout-minutes")
            if isinstance(step, dict):
                label = step.get("name") or step.get("uses") or f"#{i + 1}"
                found += _step_problems(path, text.splitlines(), f"job '{job_id}' step '{label}'", step)
    return found


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--dir", default=".github/workflows")
    p.add_argument("--actions-dir", default=".github/actions", help="composite actions, searched recursively")
    a = p.parse_args(argv)
    files = sorted(Path(a.dir).glob("*.yml")) + sorted(Path(a.dir).glob("*.yaml"))
    actions = sorted(Path(a.actions_dir).rglob("action.yml")) + sorted(Path(a.actions_dir).rglob("action.yaml"))
    found = [msg for f in files for msg in problems(f)] + [msg for f in actions for msg in action_problems(f)]
    for msg in found:
        print(f"::error::{msg}")
    print(f"checked {len(files)} workflow files and {len(actions)} actions, {len(found)} missing timeouts")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
