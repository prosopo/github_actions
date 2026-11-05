"""Tag every unit whose package.json version changed between two commits.

A unit is one tracked package.json with a version (submodules are not walked:
their repo tags its own packages). Its tag is `<unscoped name>-v<version>`,
e.g. `@prosopo/cli` 3.1.0 -> `cli-v3.1.0`. Packages in one changesets `fixed`
group share a version, so the group gets a single tag named after its first
entry instead of one per package.

Existing tags are left alone, so re-running on the same push is a no-op. A
push with no usable `before` (branch creation, all-zero sha, or a commit the
clone does not have) tags nothing rather than guessing a baseline: tagging
every package would re-trigger every publish.

Usage: tag_versions.py --before SHA --after SHA [--changeset-config PATH]
       [--push] [--remote origin]
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import subprocess
import sys
from dataclasses import dataclass

ZERO_SHA = "0" * 40


@dataclass(frozen=True)
class Unit:
    name: str
    version: str


def git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], capture_output=True, text=True, timeout=60)


def log(event: str, **fields: object) -> None:
    print(" ".join([f"event={event}", *(f"{k}={json.dumps(v)}" for k, v in fields.items())]), flush=True)


def commit_exists(sha: str) -> bool:
    return bool(sha) and sha != ZERO_SHA and git("cat-file", "-e", f"{sha}^{{commit}}").returncode == 0


def versions_at(sha: str) -> dict[str, Unit]:
    """Map each tracked package.json path at `sha` to its name and version."""
    listing = git("ls-tree", "-r", "--name-only", sha)
    listing.check_returncode()
    units: dict[str, Unit] = {}
    for path in listing.stdout.splitlines():
        if path != "package.json" and not path.endswith("/package.json"):
            continue
        if "node_modules/" in path:
            continue
        shown = git("show", f"{sha}:{path}")
        if shown.returncode != 0:
            continue
        try:
            data = json.loads(shown.stdout)
        except json.JSONDecodeError:
            continue
        name, version = data.get("name"), data.get("version")
        if isinstance(name, str) and isinstance(version, str) and name and version:
            units[path] = Unit(name, version)
    return units


def fixed_groups(config_text: str | None) -> list[list[str]]:
    if not config_text:
        return []
    fixed = json.loads(config_text).get("fixed", [])
    return [list(group) for group in fixed if group]


def tag_name(name: str) -> str:
    return name.rsplit("/", 1)[-1]


def group_of(name: str, groups: list[list[str]]) -> list[str] | None:
    for group in groups:
        if any(fnmatch.fnmatchcase(name, pattern) for pattern in group):
            return group
    return None


def tags_for(before: dict[str, Unit], after: dict[str, Unit], groups: list[list[str]]) -> list[str]:
    """Tags for units new at `after` or whose version moved, one per fixed group."""
    previous = {unit.name: unit.version for unit in before.values()}
    tags: set[str] = set()
    for unit in after.values():
        if previous.get(unit.name) == unit.version:
            continue
        group = group_of(unit.name, groups)
        label = tag_name(group[0]) if group else tag_name(unit.name)
        tags.add(f"{label}-v{unit.version}")
    return sorted(tags)


def existing_tags() -> set[str]:
    listed = git("tag", "--list")
    listed.check_returncode()
    return set(listed.stdout.split())


def write_output(tags: list[str]) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as out:
            out.write(f"tags={json.dumps(tags)}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--before", required=True)
    parser.add_argument("--after", required=True)
    parser.add_argument("--changeset-config", default=".changeset/config.json")
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--remote", default="origin")
    args = parser.parse_args(argv)

    if not commit_exists(args.before):
        log("skip", reason="no usable before commit", before=args.before)
        write_output([])
        return 0

    config = git("show", f"{args.after}:{args.changeset_config}")
    groups = fixed_groups(config.stdout if config.returncode == 0 else None)
    wanted = tags_for(versions_at(args.before), versions_at(args.after), groups)
    have = existing_tags()
    new = [tag for tag in wanted if tag not in have]
    for tag in wanted:
        log("tag", tag=tag, status="new" if tag in new else "exists")

    for tag in new:
        git("tag", tag, args.after).check_returncode()
    if new and args.push:
        pushed = git("push", args.remote, *(f"refs/tags/{tag}" for tag in new))
        if pushed.returncode != 0:
            log("push_failed", stderr=pushed.stderr.strip())
            return 1
    write_output(new)
    log("done", created=len(new), pushed=bool(new and args.push))
    return 0


if __name__ == "__main__":
    sys.exit(main())
