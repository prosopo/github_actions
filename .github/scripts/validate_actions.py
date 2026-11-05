"""Check every .github/actions/*/action.yml against the parts of GitHub's action
metadata schema that a typo silently breaks.

actionlint never reads composite action.yml files, and GitHub only rejects a
bad one when a consumer runs it, so a missing `shell:` or a misspelled `runs`
key otherwise ships to every repo pinning this one.

Usage: python3 validate_actions.py [--root .github/actions]
"""
import argparse
import sys
from pathlib import Path

import yaml

RUNNERS = {"composite", "node20", "node24", "docker"}
TOP_KEYS = {"name", "author", "description", "inputs", "outputs", "runs", "branding"}
INPUT_KEYS = {"description", "required", "default", "deprecationMessage"}
STEP_KEYS = {"id", "if", "name", "uses", "run", "shell", "with", "env",
             "working-directory", "continue-on-error", "timeout-minutes"}


def check_action(meta: object) -> list[str]:
    if not isinstance(meta, dict):
        return ["not a mapping"]
    errors = [f"unknown top-level key '{k}'" for k in meta if k not in TOP_KEYS]
    errors += [f"missing '{k}'" for k in ("name", "description", "runs") if not meta.get(k)]
    for name, spec in (meta.get("inputs") or {}).items():
        if not isinstance(spec, dict) or not spec.get("description"):
            errors.append(f"input '{name}' has no description")
            continue
        errors += [f"input '{name}': unknown key '{k}'" for k in spec if k not in INPUT_KEYS]
    composite = False
    for name, spec in (meta.get("outputs") or {}).items():
        if not isinstance(spec, dict) or not spec.get("description"):
            errors.append(f"output '{name}' has no description")
    runs = meta.get("runs")
    if isinstance(runs, dict):
        using = runs.get("using")
        if using not in RUNNERS:
            errors.append(f"runs.using '{using}' is not one of {sorted(RUNNERS)}")
        composite = using == "composite"
        if composite:
            errors += check_steps(runs.get("steps"))
    if composite:
        errors += [f"output '{n}' has no value" for n, s in (meta.get("outputs") or {}).items()
                   if isinstance(s, dict) and "value" not in s]
    return errors


def check_steps(steps: object) -> list[str]:
    if not isinstance(steps, list) or not steps:
        return ["composite action has no steps"]
    errors = []
    for i, step in enumerate(steps):
        label = f"step {i + 1}" + (f" ({step.get('name')})" if isinstance(step, dict) and step.get("name") else "")
        if not isinstance(step, dict):
            errors.append(f"{label}: not a mapping")
            continue
        errors += [f"{label}: unknown key '{k}'" for k in step if k not in STEP_KEYS]
        if ("run" in step) == ("uses" in step):
            errors.append(f"{label}: needs exactly one of 'run' or 'uses'")
        # Composite run steps have no default shell, unlike workflow steps.
        if "run" in step and not step.get("shell"):
            errors.append(f"{label}: 'run' step has no 'shell'")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path(".github/actions"))
    args = parser.parse_args(argv)
    failed = False
    files = sorted(args.root.glob("*/action.y*ml"))
    if not files:
        print(f"no action.yml under {args.root}", file=sys.stderr)
        return 1
    for path in files:
        for error in check_action(yaml.safe_load(path.read_text())):
            print(f"{path}: {error}", file=sys.stderr)
            failed = True
    print(f"checked {len(files)} actions")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
