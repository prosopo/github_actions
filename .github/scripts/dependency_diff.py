#!/usr/bin/env python3
"""Fail a PR that adds a vulnerable or disallowed-license dependency.

Compares two osv-scanner JSON reports (`scan source --format json
--licenses=...`), one of the PR base and one of its head, and reports only
what the head adds: a vulnerability at or above --severity, or a license
outside the allowlist, on a package the base did not already have it on.
Existing backlog never blocks a PR; Dependabot alerts cover that.

Private repos use this instead of actions/dependency-review-action, whose
dependency-graph compare API returns 403 there without GitHub Code Security.

A finding is keyed by (ecosystem, package, vulnerability group or license),
not version: bumping a package that stays vulnerable to the same advisory
adds nothing new. A vulnerability with no CVSS score counts as failing,
since unscored is not the same as harmless. An UNKNOWN license is reported
but does not fail: osv-scanner reports it for many packages that do declare
one in a form deps.dev cannot parse.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

SEVERITY_FLOOR = {"low": 0.1, "moderate": 4.0, "high": 7.0, "critical": 9.0}
UNKNOWN_LICENSE = "UNKNOWN"


@dataclass(frozen=True, order=True)
class Finding:
    kind: str
    ecosystem: str
    package: str
    detail: str
    version: str = ""
    blocking: bool = True

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (self.kind, self.ecosystem, self.package, self.detail)


def _score(raw: object) -> float | None:
    try:
        return float(str(raw))
    except ValueError:
        return None


def findings(report: dict, floor: float) -> list[Finding]:
    out: list[Finding] = []
    for result in report.get("results") or []:
        for entry in result.get("packages") or []:
            pkg = entry.get("package") or {}
            eco, name, version = pkg.get("ecosystem", ""), pkg.get("name", ""), pkg.get("version", "")
            for group in entry.get("groups") or []:
                score = _score(group.get("max_severity", ""))
                if score is not None and score < floor:
                    continue
                ident = ",".join(sorted(group.get("ids") or []))
                label = f"{ident} (CVSS {score})" if score is not None else f"{ident} (unscored)"
                out.append(Finding("vulnerability", eco, name, label, version))
            for lic in entry.get("license_violations") or []:
                out.append(Finding("license", eco, name, lic, version, blocking=lic != UNKNOWN_LICENSE))
    return out


def added(base: list[Finding], head: list[Finding]) -> list[Finding]:
    seen = {f.key for f in base}
    unique: dict[tuple[str, str, str, str], Finding] = {}
    for f in head:
        if f.key not in seen:
            unique.setdefault(f.key, f)
    return sorted(unique.values())


def render(new: list[Finding]) -> list[str]:
    if not new:
        return ["No new vulnerable or disallowed-license dependencies."]
    lines = ["| | Kind | Package | Detail |", "|---|---|---|---|"]
    for f in new:
        mark = "❌" if f.blocking else "⚠️"
        lines.append(f"| {mark} | {f.kind} | {f.ecosystem} {f.package}@{f.version} | {f.detail} |")
    return lines


def _load(path: Path) -> dict:
    # osv-scanner writes nothing when the tree has no lockfiles.
    text = path.read_text()
    return json.loads(text) if text.strip() else {}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", type=Path, required=True, help="osv-scanner JSON of the PR base")
    parser.add_argument("--head", type=Path, required=True, help="osv-scanner JSON of the PR head")
    parser.add_argument("--severity", choices=sorted(SEVERITY_FLOOR), default="high")
    parser.add_argument("--summary-file", type=Path, help="append the markdown table here")
    args = parser.parse_args(argv)

    floor = SEVERITY_FLOOR[args.severity]
    new = added(findings(_load(args.base), floor), findings(_load(args.head), floor))
    lines = render(new)
    if args.summary_file:
        with args.summary_file.open("a") as fh:
            fh.write("## Dependency review\n\n" + "\n".join(lines) + "\n")
    blocking = [f for f in new if f.blocking]
    for f in new:
        print(json.dumps({"event": "dependency_added", "kind": f.kind, "ecosystem": f.ecosystem,
                          "package": f.package, "version": f.version, "detail": f.detail,
                          "blocking": f.blocking}))
    print(json.dumps({"event": "dependency_review", "added": len(new), "blocking": len(blocking)}))
    return 1 if blocking else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
