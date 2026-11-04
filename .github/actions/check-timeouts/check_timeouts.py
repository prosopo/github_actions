"""Fail when a workflow job or step has no timeout-minutes.

GitHub's default is 360 minutes per job, and a hung step on a self-hosted
runner holds that VM for all of it. Jobs that call a reusable workflow
(`uses:` at job level) are exempt: GitHub rejects timeout-minutes there, and
the called workflow's own jobs are checked where they are defined.
"""

import argparse
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


def problems(path: Path) -> list[str]:
    doc = yaml.load(path.read_text(), Loader=LineLoader) or {}
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
    return found


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--dir", default=".github/workflows")
    a = p.parse_args(argv)
    files = sorted(Path(a.dir).glob("*.yml")) + sorted(Path(a.dir).glob("*.yaml"))
    found = [msg for f in files for msg in problems(f)]
    for msg in found:
        print(f"::error::{msg}")
    print(f"checked {len(files)} workflow files, {len(found)} missing timeouts")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
