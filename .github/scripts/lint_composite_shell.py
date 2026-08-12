"""Shellcheck the `run:` blocks of every composite action in this repo.

actionlint already does this for workflow files, but it ignores action.yml
entirely -- and composite actions are what this repo is made of, so without
this their shell would never be linted at all.

Two things make a naive `shellcheck <(yq .run)` unusable:

* `${{ ... }}` is not shell syntax. Left alone it produces a parse error in
  every file, which drowns out real findings. It is rewritten to a plain
  placeholder variable so the surrounding shell still parses and quoting
  problems around the expression are still reported.
* A composite step may declare `shell: pwsh`/`python`/etc. Only bash and sh
  steps are handed to shellcheck; the rest are skipped rather than
  mis-analysed.

Exits non-zero if shellcheck reports anything.
"""

import pathlib
import re
import subprocess
import sys
import tempfile

import yaml

EXPRESSION = re.compile(r"\$\{\{.*?\}\}", re.DOTALL)
SHELLS = {"bash", "sh"}
ROOT = pathlib.Path(__file__).resolve().parents[2]


def check(path: pathlib.Path, index: int, shell: str, script: str) -> bool:
    """Run shellcheck over one step's script. True if it is clean."""
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as handle:
        handle.write(f"#!/usr/bin/env {shell}\n")
        handle.write(EXPRESSION.sub("$GITHUB_EXPRESSION", script))
        temp = pathlib.Path(handle.name)
    try:
        result = subprocess.run(
            ["shellcheck", "--shell", shell, "--color=always", str(temp)],
            capture_output=True,
            text=True,
        )
    finally:
        temp.unlink(missing_ok=True)
    if result.returncode == 0:
        return True
    rel = path.relative_to(ROOT)
    print(f"::group::{rel} (step {index})")
    print(result.stdout.replace(str(temp), f"{rel}:step-{index}"), end="")
    print(result.stderr, end="")
    print("::endgroup::")
    return False


def main() -> int:
    clean = True
    found = 0
    for path in sorted(ROOT.rglob("action.yml")) + sorted(ROOT.rglob("action.yaml")):
        document = yaml.safe_load(path.read_text())
        if not isinstance(document, dict):
            continue
        runs = document.get("runs")
        if not isinstance(runs, dict) or runs.get("using") != "composite":
            continue
        for index, step in enumerate(runs.get("steps") or []):
            if not isinstance(step, dict) or "run" not in step:
                continue
            shell = str(step.get("shell", "bash"))
            if shell not in SHELLS:
                continue
            found += 1
            clean &= check(path, index, shell, str(step["run"]))

    # An empty repo is a pass, not a silent no-op: say so, so a future change
    # that stops the walk finding anything is visible rather than green.
    print(f"shellchecked {found} composite step(s)")
    return 0 if clean else 1


if __name__ == "__main__":
    sys.exit(main())
