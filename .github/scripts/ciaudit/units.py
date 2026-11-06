"""Discover code units (packages, crates, Python projects, images, ...) in a repo snapshot.

A unit is identified by (repo, relpath, kind). Snapshots may contain symlinked
submodule mounts (captcha-private/captcha -> captcha snapshot); a unit found
through a mount belongs to the repo the mount points at.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import tomllib
import yaml

SKIP_DIRS = {
    "node_modules",
    ".git",
    "vendor",
    "target",
    "dist",
    "venv",
    ".venv",
    "__pycache__",
    "build",
    ".turbo",
    ".next",
    ".astro",
    "coverage",
    ".pnpm-store",
    "site-packages",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    ".gradle",
    "Pods",
    "DerivedData",
}

CATEGORIES = ("build", "lint", "typecheck", "test", "e2e")


@dataclass
class Unit:
    repo: str
    path: str  # relative to the repo snapshot root, "." for the root
    kind: str  # node | rust | python | go | php | docker | ansible | workflows | android | ios
    name: str
    scripts: dict[str, str] = field(default_factory=dict)
    deps: set[str] = field(default_factory=set)  # dependency names (node: all deps; filtered later)
    meta: dict[str, object] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.repo}:{self.kind}:{self.path}"

    @property
    def language(self) -> str:
        return {
            "node": "TS" if self.meta.get("ts") else "JS",
            "rust": "Rust",
            "python": "Python",
            "go": "Go",
            "php": "PHP",
            "docker": "Dockerfile",
            "ansible": "Ansible",
            "workflows": "GH Actions YAML",
            "android": "Kotlin/Java",
            "ios": "Swift",
        }.get(self.kind, self.kind)


def load_jsonc(text: str) -> object:
    """Parse JSON with // and /* */ comments and trailing commas (turbo.json, biome.json)."""
    out, i, n, in_str = [], 0, len(text), False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
            out.append(c)
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
            continue
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        else:
            out.append(c)
        i += 1
    cleaned = re.sub(r",(\s*[}\]])", r"\1", "".join(out))
    return json.loads(cleaned)


def walk(root: Path):
    """Yield (relative dir, filenames) following symlinked mounts, skipping build output."""
    for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".") or d == ".github")
        rel = os.path.relpath(dirpath, root)
        yield ("." if rel == "." else rel.replace(os.sep, "/")), filenames


def _join(d: str, f: str) -> str:
    return f if d == "." else f"{d}/{f}"


def has_files(root: Path, rel: str, exts: tuple[str, ...], limit: int = 2000) -> bool:
    seen = 0
    for _d, files in walk(root / rel):
        for f in files:
            seen += 1
            if f.endswith(exts) and not f.endswith(".d.ts"):
                return True
            if seen > limit:
                return False
    return False


class Mounts:
    """Map paths inside a snapshot to the repo that owns them (submodule mounts)."""

    def __init__(self, repo: str, mounts: dict[str, str] | None = None):
        self.repo = repo
        self.mounts = dict(sorted((mounts or {}).items(), key=lambda kv: -len(kv[0])))

    def owner(self, rel: str) -> tuple[str, str]:
        for prefix, repo in self.mounts.items():
            if rel == prefix or rel.startswith(prefix + "/"):
                sub = rel[len(prefix) :].lstrip("/") or "."
                return repo, sub
        return self.repo, rel


def discover(root: Path, repo: str, mounts: dict[str, str] | None = None) -> list[Unit]:
    m = Mounts(repo, mounts)
    units: list[Unit] = []
    py_projects: list[str] = []
    loose_py: dict[str, int] = {}

    def add(rel: str, kind: str, name: str, **kw: object) -> Unit:
        owner, sub = m.owner(rel)
        u = Unit(owner, sub, kind, name, **kw)  # type: ignore[arg-type]
        u.meta["snap_path"] = rel  # path inside *this* snapshot, used to resolve CI commands
        units.append(u)
        return u

    for d, files in walk(root):
        fs = set(files)
        if m.owner(d)[1] == ".github/workflows" and any(f.endswith((".yml", ".yaml")) for f in files):
            add(d, "workflows", "workflows")
        if "package.json" in fs:
            try:
                pj = json.loads((root / d / "package.json").read_text())
            except (json.JSONDecodeError, UnicodeDecodeError):
                pj = {}
            if isinstance(pj, dict) and (pj.get("name") or pj.get("scripts")):
                deps: set[str] = set()
                for k in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
                    deps |= set((pj.get(k) or {}).keys())
                ts = "tsconfig.json" in fs or has_files(root, d, (".ts", ".tsx"), limit=400)
                cfg = {f for f in fs if re.match(r"(cypress|playwright)\.config\.", f)}
                add(
                    d,
                    "node",
                    pj.get("name") or d,
                    scripts=dict(pj.get("scripts") or {}),
                    deps=deps,
                    meta={"ts": ts, "e2e_config": sorted(cfg), "tsconfig": "tsconfig.json" in fs, "private": bool(pj.get("private"))},
                )
        if "Cargo.toml" in fs:
            try:
                ct = tomllib.loads((root / d / "Cargo.toml").read_text())
            except tomllib.TOMLDecodeError:
                ct = {}
            if "package" in ct:
                add(d, "rust", ct["package"].get("name", d))
        if "pyproject.toml" in fs or "setup.py" in fs:
            py_projects.append(d)
        if any(f.endswith(".py") for f in files):
            loose_py[d] = loose_py.get(d, 0) + sum(f.endswith(".py") for f in files)
        if "go.mod" in fs:
            add(d, "go", d)
        if "composer.json" in fs:
            add(d, "php", d)
        if "Package.swift" in fs or any(f.endswith(".xcodeproj") for f in fs):
            add(d, "ios", d)
        if "settings.gradle" in fs or "settings.gradle.kts" in fs:
            add(d, "android", d)
        if "ansible.cfg" in fs:
            add(d, "ansible", d)
        for f in files:
            if (f == "Dockerfile" or f.startswith("Dockerfile.") or f.endswith(".Dockerfile")) and not f.endswith(".dockerignore"):
                add(_join(d, f), "docker", _join(d, f))

    # .xcodeproj is a directory; walk() lists dirs only through dirnames, so re-check.
    for d, _ in list(walk(root)):
        if d.endswith(".xcodeproj"):
            parent = d.rsplit("/", 1)[0] if "/" in d else "."
            if not any(u.kind == "ios" and u.meta["snap_path"] == parent for u in units):
                add(parent, "ios", parent)

    for d in py_projects:
        add(d, "python", d, meta={"manifest": True})
    # Python files outside any declared project are grouped by their top-level
    # directory (two levels under a mount), so research/ scripts form one unit.
    covered = set(py_projects)
    groups: dict[str, int] = {}
    for d, n in loose_py.items():
        # A root pyproject (often just a uv workspace) does not absorb loose scripts.
        if any(d == p or d.startswith(p + "/") for p in covered if p != "."):
            continue
        if "/.github/" in f"/{d}/" and "/actions/" not in f"/{d}/" and "/scripts" not in f"/{d}":
            continue
        owner, sub = m.owner(d)
        prefix = d[: len(d) - len(sub)].rstrip("/") if sub != d else ""
        parts = sub.split("/")
        top = parts[0] if parts[0] != "." else "."
        if top == ".github" and len(parts) > 1:
            top = "/".join(parts[:3]) if parts[1] == "actions" else "/".join(parts[:2])
        g = f"{prefix}/{top}".lstrip("/") if prefix else top
        groups[g] = groups.get(g, 0) + n
    if "." in covered:
        groups.pop(".", None)  # root-level .py files belong to the root project
    for g, n in sorted(groups.items()):
        add(g, "python", g, meta={"manifest": False, "py_files": n})
    return units


def match_globs(path: str, globs: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, g) or fnmatch.fnmatch(path + "/", g) or fnmatch.fnmatch(path + "/x", g) for g in globs)


def pnpm_workspace(root: Path) -> list[str] | None:
    """Return workspace globs (with ! excludes), or None if the root is not a JS workspace."""
    f = root / "pnpm-workspace.yaml"
    if f.exists():
        doc = yaml.safe_load(f.read_text()) or {}
        return list(doc.get("packages") or [])
    pj = root / "package.json"
    if pj.exists():
        try:
            ws = json.loads(pj.read_text()).get("workspaces")
        except json.JSONDecodeError:
            return None
        if isinstance(ws, dict):
            ws = ws.get("packages")
        if ws:
            return list(ws)
    return None


def workspace_glob_match(rel: str, glob: str) -> bool:
    """pnpm/npm workspace glob semantics: `*` stays within one path segment, `**` spans any number.

    fnmatch alone lets `*` cross `/`, which made `captcha/integration/*` claim packages nested deeper.
    """
    def match(parts: list[str], pats: list[str]) -> bool:
        if not pats:
            return not parts
        if pats[0] == "**":
            return any(match(parts[i:], pats[1:]) for i in range(len(parts) + 1))
        return bool(parts) and fnmatch.fnmatchcase(parts[0], pats[0]) and match(parts[1:], pats[1:])
    return match(rel.split("/"), glob.strip("/").split("/"))


def in_workspace(rel: str, globs: list[str]) -> bool:
    inc = [g for g in globs if not g.startswith("!")]
    exc = [g[1:] for g in globs if g.startswith("!")]
    if rel == ".":
        return True
    return any(workspace_glob_match(rel, g) for g in inc) and not any(workspace_glob_match(rel, g) for g in exc)
