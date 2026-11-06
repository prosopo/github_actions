#!/usr/bin/env python3
"""Fail when an ansible inventory holds a sensitive value in plaintext.

Walks <inventory-dir>/<env>/{group,host}_vars/**/*.yml and checks every leaf key
against a classification file:

    sensitive: [keys whose values must be inline !vault blocks]
    plain: [keys that are safe in plaintext]
    pending_encryption: [secrets known to be committed in plaintext]

Every leaf key must be in exactly one list, so a new variable forces an explicit
decision instead of defaulting to plaintext. `pending_encryption` exists so a
repo can adopt the check while it still has plaintext secrets: they are
reported as warnings, and moving a key to `sensitive` once encrypted makes the
check enforce it from then on.

Needs only PyYAML and no vault password: !vault tags are recognised, never
decrypted, so it is safe on untrusted PRs.

With --allow-references a sensitive value also passes when it is a Jinja
(`{{ ... }}`) or compose (`${...}`) reference, because it then names a secret
held elsewhere rather than holding one. Off by default: a literal around the
reference (`pass{{ x }}`) would pass too.

Same checks as captcha-private's vault_tool.py `check`, minus its
repo-specific layout, so any repo can run it through the action.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ENCRYPTED = object()
REFERENCE = re.compile(r"\{\{.*\}\}|\$\{[^}]+\}", re.DOTALL)


class _VaultMarkerLoader(yaml.SafeLoader):
    """SafeLoader that turns !vault nodes into the ENCRYPTED marker.

    Subclassed so the constructor does not leak into other yaml.safe_load calls.
    """


_VaultMarkerLoader.add_constructor("!vault", lambda _loader, _node: ENCRYPTED)


class CheckError(Exception):
    """A configuration problem that stops the check before any file is read."""


@dataclass
class Classification:
    sensitive: set[str]
    plain: set[str]
    pending: set[str]

    @classmethod
    def load(cls, path: Path) -> Classification:
        doc = yaml.safe_load(path.read_text()) or {}
        if not isinstance(doc, dict):
            raise CheckError(f"{path}: not a YAML mapping")
        lists = {
            name: set(doc.get(name) or [])
            for name in ("sensitive", "plain", "pending_encryption")
        }
        names = list(lists)
        for i, first in enumerate(names):
            for second in names[i + 1 :]:
                overlap = lists[first] & lists[second]
                if overlap:
                    raise CheckError(
                        f"{path}: keys in both {first} and {second}: {sorted(overlap)}"
                    )
        return cls(lists["sensitive"], lists["plain"], lists["pending_encryption"])


@dataclass
class Report:
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def flatten(data: Any, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, dict):
                out.update(flatten(value, f"{prefix}{key}."))
            else:
                out[f"{prefix}{key}"] = value
    return out


def vars_files(inventory_dir: Path, envs: list[str]) -> list[Path]:
    if not envs:
        envs = sorted(p.name for p in inventory_dir.iterdir() if p.is_dir())
    found: list[Path] = []
    for env in envs:
        for kind in ("group_vars", "host_vars"):
            base = inventory_dir / env / kind
            found.extend(sorted(base.rglob("*.yml")))
            found.extend(sorted(base.rglob("*.yaml")))
    return found


def is_protected(value: Any, allow_references: bool) -> bool:
    if value is ENCRYPTED:
        return True
    return allow_references and isinstance(value, str) and bool(REFERENCE.search(value))


def check_file(
    path: Path,
    rel: str,
    classification: Classification,
    allow_references: bool,
    report: Report,
) -> None:
    text = path.read_text()
    if text.lstrip().startswith("$ANSIBLE_VAULT"):
        report.failures.append(
            f"{rel}: whole-file encrypted; convert it to inline !vault strings"
        )
        return
    data = yaml.load(text, Loader=_VaultMarkerLoader)
    if data is None:
        return
    if not isinstance(data, dict):
        report.failures.append(f"{rel}: not a YAML mapping")
        return
    for key, value in flatten(data).items():
        leaf = key.rsplit(".", 1)[-1]
        if leaf in classification.sensitive:
            if not is_protected(value, allow_references):
                report.failures.append(f"{rel}: sensitive key '{key}' is not encrypted")
        elif leaf in classification.pending:
            if not is_protected(value, allow_references):
                report.warnings.append(
                    f"{rel}: '{key}' is a plaintext secret pending encryption"
                )
        elif leaf not in classification.plain:
            report.failures.append(f"{rel}: unclassified key '{key}'")


def run(
    inventory_dir: Path,
    classification_path: Path,
    envs: list[str],
    allow_references: bool,
    root: Path,
) -> Report:
    classification = Classification.load(classification_path)
    if not inventory_dir.is_dir():
        raise CheckError(f"{inventory_dir}: inventory directory not found")
    files = vars_files(inventory_dir, envs)
    if not files:
        raise CheckError(f"no vars files under {inventory_dir} for envs {envs or 'all'}")
    report = Report()
    for path in files:
        try:
            rel = str(path.resolve().relative_to(root.resolve()))
        except ValueError:
            rel = str(path)
        check_file(path, rel, classification, allow_references, report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--inventory-dir", required=True, type=Path)
    parser.add_argument("--classification", required=True, type=Path)
    parser.add_argument(
        "--env",
        action="append",
        default=[],
        help="environment subdirectory to check; repeatable (default: every one)",
    )
    parser.add_argument("--allow-references", action="store_true")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    try:
        report = run(
            args.inventory_dir,
            args.classification,
            args.env,
            args.allow_references,
            args.root,
        )
    except CheckError as error:
        print(f"::error::{error}")
        return 2
    for warning in report.warnings:
        print(f"::warning::{warning}")
    for failure in report.failures:
        print(f"::error::{failure}")
    if report.failures:
        print(f"{len(report.failures)} problem(s) found", file=sys.stderr)
        return 1
    print("all vars files pass encryption checks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
