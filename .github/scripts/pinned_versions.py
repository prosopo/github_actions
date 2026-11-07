#!/usr/bin/env python3
"""Fail on any dependency that is not pinned to one exact version.

Walks a repo (skipping build output, virtualenvs and git submodules, which
are checked in their own repo) and checks:

- package.json dependencies/devDependencies/optionalDependencies: "1.2.3",
  workspace:* or workspace:1.2.3, npm:alias@1.2.3, file:/link:, or a git/URL
  spec ending in a 40-hex commit. peerDependencies are exempt: they state what
  a consumer may install, so a range is the point.
- package-lock.json and pnpm-lock.yaml: every registry package carries an
  integrity hash, so a re-publish under the same version cannot slip in.
- Cargo.toml [*dependencies] and [workspace.dependencies]: "=1.2.3". A bare
  "1.2" is a caret range in cargo. path, `workspace = true` and git deps with
  `rev` need no version; a version given alongside path must still be exact.
- pyproject.toml [project] dependencies and optional-dependencies,
  [dependency-groups], and requirements*.txt: "name==1.2.3" (extras and
  markers allowed) or "name @ <url>@<40-hex>". [build-system] requires is
  exempt: build backends are resolved per build, outside the lockfile.

Run from the repo root: python3 pinned_versions.py --root .
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import sys
from collections.abc import Iterator
from pathlib import Path

import tomllib

IGNORE_DIRS = {
    "node_modules", ".git", "dist", "build", ".next", ".astro", ".turbo", ".nx",
    "coverage", ".cache", ".venv", "venv", "target", "__pycache__",
}
NPM_SECTIONS = ("dependencies", "devDependencies", "optionalDependencies")
SEMVER = r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?"
EXACT_SEMVER = re.compile(rf"^{SEMVER}$")
EXACT_CARGO = re.compile(rf"^={SEMVER}$")
GIT_COMMIT = re.compile(r"[#@][0-9a-f]{40}$")
PY_NAME = r"[A-Za-z0-9][A-Za-z0-9._-]*(?:\[[^\]]*\])?"
PY_EXACT = re.compile(rf"^{PY_NAME}\s*===?\s*[0-9][^\s,;]*\s*(?:;.*)?$")
PY_URL = re.compile(rf"^{PY_NAME}\s*@\s*\S+[#@][0-9a-f]{{40}}\s*(?:;.*)?$")


def npm_pinned(spec: object) -> bool:
    v = str(spec).strip()
    if EXACT_SEMVER.match(v) or v.startswith(("file:", "link:")):
        return True
    if v.startswith("workspace:"):
        rest = v.removeprefix("workspace:")
        return rest == "*" or bool(EXACT_SEMVER.match(rest))
    if v.startswith("npm:"):
        at = v.rfind("@")
        return at > len("npm:") and bool(EXACT_SEMVER.match(v[at + 1:]))
    if re.match(r"^(git\+|git:|github:|https?:)", v):
        return bool(GIT_COMMIT.search(v))
    return False


def cargo_pinned(spec: object) -> bool:
    if isinstance(spec, str):
        return bool(EXACT_CARGO.match(spec.strip()))
    if not isinstance(spec, dict):
        return False
    if spec.get("workspace") is True:
        return True
    if "git" in spec:
        return "rev" in spec and re.fullmatch(r"[0-9a-f]{40}", str(spec["rev"])) is not None
    if "version" in spec:
        return bool(EXACT_CARGO.match(str(spec["version"]).strip()))
    return "path" in spec


def python_pinned(req: str) -> bool:
    req = req.strip()
    return bool(PY_EXACT.match(req) or PY_URL.match(req))


def check_package_json(path: Path, rel: str) -> Iterator[str]:
    try:
        pkg = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        yield f"{rel}: invalid JSON ({exc})"
        return
    for section in NPM_SECTIONS:
        for name, spec in (pkg.get(section) or {}).items():
            if not npm_pinned(spec):
                yield f'{rel}  {section} > "{name}": "{spec}" is not an exact version'


def check_npm_lock(path: Path, rel: str) -> Iterator[str]:
    lock = json.loads(path.read_text())
    if lock.get("lockfileVersion", 0) < 2:
        yield f"{rel}: lockfileVersion {lock.get('lockfileVersion')} is too old; needs >= 2 for integrity hashes"
        return
    for key, entry in (lock.get("packages") or {}).items():
        if not key or "node_modules/" not in key or entry.get("link") is True:
            continue
        resolved = entry.get("resolved", "")
        registry = resolved == "" or re.match(r"^https?://[^/]*registry\.", resolved)
        if registry and not entry.get("integrity"):
            yield f"{rel}  {key}: missing integrity hash"


def pnpm_resolutions(text: str) -> Iterator[tuple[str, str]]:
    """Yield (package key, resolution text) from a pnpm-lock.yaml.

    Line-based rather than a YAML parse (stdlib has none): under `packages:`
    each key sits at two spaces and its `resolution:` is either an inline
    `{integrity: ...}` map or a block whose keys sit at six spaces.
    """
    section, key, block = "", "", None
    for line in text.splitlines():
        if line and not line.startswith(" "):
            section = line.rstrip(":")
            continue
        if section != "packages":
            continue
        if line.startswith("  ") and not line.startswith("   ") and line.rstrip().endswith(":"):
            if block is not None:
                yield key, block
            key, block = line.strip().rstrip(":").strip("'\""), None
        elif line.startswith("    resolution:"):
            block = line.split(":", 1)[1].strip()
        elif block is not None and line.startswith("      "):
            block += " " + line.strip()
    if block is not None:
        yield key, block


def check_pnpm_lock(path: Path, rel: str) -> Iterator[str]:
    for key, resolution in pnpm_resolutions(path.read_text()):
        if not any(tag in resolution for tag in ("integrity:", "type:", "tarball:")):
            yield f"{rel}  {key}: missing integrity hash"


def _cargo_tables(doc: dict) -> Iterator[tuple[str, dict]]:
    for name in ("dependencies", "dev-dependencies", "build-dependencies"):
        if isinstance(doc.get(name), dict):
            yield name, doc[name]
    if isinstance(doc.get("workspace", {}).get("dependencies"), dict):
        yield "workspace.dependencies", doc["workspace"]["dependencies"]
    for target, body in (doc.get("target") or {}).items():
        for name, table in _cargo_tables(body):
            if not name.startswith("workspace"):
                yield f"target.{target}.{name}", table


def check_cargo(path: Path, rel: str) -> Iterator[str]:
    doc = tomllib.loads(path.read_text())
    for section, table in _cargo_tables(doc):
        for name, spec in table.items():
            if not cargo_pinned(spec):
                yield f'{rel}  [{section}] {name}: {json.dumps(spec)} is not an exact "=x.y.z" version'


def check_pyproject(path: Path, rel: str) -> Iterator[str]:
    doc = tomllib.loads(path.read_text())
    project = doc.get("project") or {}
    groups: list[tuple[str, list]] = [("project.dependencies", project.get("dependencies") or [])]
    groups += [(f"project.optional-dependencies.{k}", v) for k, v in (project.get("optional-dependencies") or {}).items()]
    groups += [(f"dependency-groups.{k}", v) for k, v in (doc.get("dependency-groups") or {}).items()]
    for section, reqs in groups:
        for req in reqs:
            # {include-group = "..."} entries pull in another group, checked on its own.
            if isinstance(req, str) and not python_pinned(req):
                yield f'{rel}  [{section}] "{req}" is not pinned with =='


def check_requirements(path: Path, rel: str) -> Iterator[str]:
    for number, raw in enumerate(path.read_text().splitlines(), 1):
        line = raw.split(" #", 1)[0].strip()
        if not line or line.startswith(("#", "-r", "-c", "--")):
            continue
        if not python_pinned(line.removeprefix("-e ").strip()):
            yield f'{rel}:{number}  "{line}" is not pinned with =='


CHECKS = {
    "package.json": check_package_json,
    "package-lock.json": check_npm_lock,
    "pnpm-lock.yaml": check_pnpm_lock,
    "Cargo.toml": check_cargo,
    "pyproject.toml": check_pyproject,
}


def submodules(root: Path) -> set[Path]:
    gitmodules = root / ".gitmodules"
    if not gitmodules.exists():
        return set()
    found = re.findall(r"^\s*path\s*=\s*(.+?)\s*$", gitmodules.read_text(), re.M)
    return {(root / p).resolve() for p in found}


def manifests(root: Path, exclude: list[str]) -> Iterator[Path]:
    skip = submodules(root)
    stack = [root]
    while stack:
        current = stack.pop()
        for entry in sorted(current.iterdir()):
            rel = entry.relative_to(root).as_posix()
            if any(fnmatch.fnmatch(rel, pat) for pat in exclude):
                continue
            if entry.is_dir():
                if entry.name not in IGNORE_DIRS and entry.resolve() not in skip:
                    stack.append(entry)
            elif entry.name in CHECKS or fnmatch.fnmatch(entry.name, "requirements*.txt"):
                yield entry


def check(root: Path, exclude: list[str]) -> tuple[int, list[str]]:
    count, errors = 0, []
    for path in manifests(root, exclude):
        count += 1
        rel = path.relative_to(root).as_posix()
        fn = CHECKS.get(path.name, check_requirements)
        try:
            errors.extend(fn(path, rel))
        except (ValueError, OSError) as exc:
            errors.append(f"{rel}: cannot parse ({exc})")
    return count, errors


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--exclude", action="append", default=[], help="repo-relative glob to skip; repeatable")
    args = parser.parse_args(argv)
    count, errors = check(args.root.resolve(), args.exclude)
    for err in errors:
        print(f"::error::{err}")
    print(json.dumps({"event": "pinned_versions", "manifests": count, "errors": len(errors)}))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
