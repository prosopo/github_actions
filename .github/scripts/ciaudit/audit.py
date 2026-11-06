"""CI coverage audit: which units does CI build, lint, typecheck, test and e2e-test?

Usage:
  python3 fetch.py --dest-prefix SNAP/stage- --repos-file repos.txt
  python3 audit.py --snap-prefix SNAP/stage- --repos-file repos.txt \
      --mount captcha-private:captcha=captcha --mount captcha-private:protect=Protect \
      --out-json report.json --out-html report.html [--verify --api-cache api.json]

Pipeline: units.discover -> workflows.Walker (static interpretation of every
triggered workflow, following reusable workflows, composite actions, package
scripts, turbo dependsOn and filters) -> verify.Verifier (latest green run's
job/step conclusions) -> matrix + gaps.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import posixpath
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from cmds import Ctx
from units import CATEGORIES, Unit, discover
from verify import Check, Verifier, gh_api_factory
from workflows import CI_TRIGGERS, WILD, Evidence, Walker


@dataclass
class Cell:
    state: str  # yes | unverified | manual | no | n/a | opt-out
    evidence: list[dict] = field(default_factory=list)
    reason: str = ""


def applicable(u: Unit, cat: str) -> tuple[bool, str]:
    """Whether a category applies to a unit kind; the reason is shown for n/a."""
    k = u.kind
    if k == "node":
        if cat == "e2e":
            has = any("e2e" in s or "cypress" in s or "playwright" in s for s in u.scripts) or bool(u.meta.get("e2e_config"))
            return has, "" if has else "no e2e suite or config in package"
        if cat == "typecheck" and not u.meta.get("ts"):
            return False, "no TypeScript"
        return True, ""
    table = {
        "rust": {"build", "lint", "test"},
        "go": {"build", "lint", "test"},
        "python": {"lint", "test", "typecheck"},
        "php": {"lint", "test"},
        "docker": {"build", "lint"},
        "ansible": {"lint", "typecheck"},
        "workflows": {"lint"},
        "android": {"build", "lint", "test"},
        "ios": {"build", "lint", "test"},
    }
    ok = cat in table.get(k, set())
    if k == "rust" and cat == "typecheck":
        return False, "compiler typechecks (covered by build/clippy)"
    return ok, "" if ok else f"not meaningful for {k}"


def recommend(u: Unit, cat: str, cell: Cell) -> str:
    if cell.state == "unverified":
        return "verify: static analysis says CI runs it, but no green run shows the step succeeding — check the job's if:/paths filter"
    if cell.state == "manual":
        return "add to PR/push CI: today only a manual/scheduled/release workflow runs it"
    k = u.kind
    if k == "node":
        have = [s for s in u.scripts if _cat_of_name(s) == cat]
        if have:
            return f"add: script(s) {', '.join(have[:3])} exist but no workflow reaches them — add the package to the CI filter list"
        return {
            "test": "add a test script and suite, or opt out if the package has no runtime logic (config/types only)",
            "lint": "add: package is outside the biome includes and has no lint script",
            "typecheck": "add a typecheck (or build:tsc) script so turbo typecheck covers it",
            "build": "add a build script, or opt out if consumers use the source directly",
            "e2e": "add the e2e suite to a workflow, or opt out with reason",
        }[cat]
    return {
        ("rust", "build"): "add cargo build --locked to CI",
        ("rust", "lint"): "add cargo clippy -D warnings and cargo fmt --check",
        ("rust", "test"): "add cargo test --locked",
        ("go", "lint"): "add go vet / golangci-lint",
        ("go", "test"): "add go test ./...",
        ("go", "build"): "add go build ./...",
        ("python", "lint"): "add ruff check (repo ruff config) to CI, or opt out for throwaway research code",
        ("python", "test"): "add python -m unittest/pytest job, or opt out if these are one-off scripts with no tests",
        ("python", "typecheck"): "add mypy/pyright, or opt out (untyped scripts)",
        ("php", "lint"): "add phpcs (WordPress standard) to CI",
        ("php", "test"): "add phpunit, or rely on e2e and opt out",
        ("docker", "build"): "add a PR-time docker build (no push), or opt out if it is only built by a deploy pipeline",
        ("docker", "lint"): "add hadolint, or opt out",
        ("ansible", "lint"): "add ansible-lint",
        ("ansible", "typecheck"): "add ansible-playbook --syntax-check, or opt out (ansible-lint covers syntax)",
        ("workflows", "lint"): "add an actionlint job (workflow-lint policy)",
        ("android", "build"): "add gradle assemble",
        ("android", "lint"): "add gradle lint/ktlint",
        ("android", "test"): "add gradle test",
        ("ios", "build"): "add xcodebuild/swift build",
        ("ios", "lint"): "add swiftlint",
        ("ios", "test"): "add xcodebuild/swift test",
    }.get((k, cat), "add, or opt out with reason")


def _cat_of_name(script: str) -> str | None:
    from cmds import name_categories

    cs = name_categories(script)
    return sorted(cs)[0] if len(cs) == 1 else ("typecheck" if "typecheck" in cs else None)


def link_for(ev: Evidence, check: Check | None, owner: str) -> dict:
    r = ev.ref
    d = {
        "repo": r.repo,
        "workflow": r.workflow,
        "job": " / ".join(r.job).replace(WILD, "*"),
        "step": r.step.replace(WILD, "*"),
        "via": ev.hit.via,
        "triggers": list(r.triggers),
        "workflow_url": f"https://github.com/{owner}/{r.repo}/blob/HEAD/.github/workflows/{r.workflow}",
    }
    if check:
        d.update(status=check.status, run_url=check.run_url, job_url=check.job_url, source=check.source, detail=check.detail)
    return d


def build_matrix(
    units: dict[str, Unit], evidence: list[Evidence], checks: dict[int, Check] | None, owner: str
) -> dict[str, dict[str, Cell]]:
    by: dict[tuple[str, str], list[Evidence]] = defaultdict(list)
    for e in evidence:
        by[(e.hit.unit, e.hit.category)].append(e)
    out: dict[str, dict[str, Cell]] = {}
    for key, u in units.items():
        row: dict[str, Cell] = {}
        for cat in CATEGORIES:
            evs = by.get((key, cat), [])
            ok, why = applicable(u, cat)
            if not evs:
                row[cat] = Cell("no" if ok else "n/a", reason=why)
                continue
            uniq: dict[tuple, Evidence] = {}
            for e in evs:
                uniq.setdefault((e.ref.repo, e.ref.workflow, e.ref.job, e.ref.index), e)
            links = [link_for(e, checks.get(id(e)) if checks is not None else None, owner) for e in uniq.values()]
            ci = [x for x in links if set(x["triggers"]) & CI_TRIGGERS]
            static = [x for x in ci if "{dynamic}" not in x["via"]]  # runtime-built target lists prove nothing per unit
            if not ci:
                state = "manual"
            elif not static:
                state = "unverified"
            elif checks is None:
                state = "yes"
            else:
                state = "yes" if any(x.get("status") == "success" for x in static) else "unverified"
            links.sort(key=lambda x: (x.get("status") != "success", not (set(x["triggers"]) & CI_TRIGGERS)))
            row[cat] = Cell(state, links[:6])
        out[key] = row
    return out


OPT_OUT = re.compile(r"#\s*(\w+) (\w+) opt-out:\s*(\S+)\s+--\s+(\S.*)$")


def opt_outs(root: Path, repo: str) -> dict[tuple[str, str], str]:
    """Opt-outs recorded in a repo's workflows, as {(unit key, category): reason}.

    A workflow records one with a comment line
    `# <kind> <category> opt-out: <path> -- <reason>`, e.g.
    `# docker build opt-out: demos/x/Dockerfile -- built by e2e.yml after the bundle build`.
    """
    out: dict[tuple[str, str], str] = {}
    wf = root / ".github" / "workflows"
    for f in sorted(wf.glob("*.y*ml")) if wf.is_dir() else []:
        for line in f.read_text(errors="replace").splitlines():
            m = OPT_OUT.search(line)
            if m:
                kind, cat, path, why = m.groups()
                out[(f"{repo}:{kind}:{posixpath.normpath(path)}", cat)] = f"opt-out ({f.name}): {why.strip()}"
    return out


def file_opt_outs(path: Path, units: dict[str, Unit]) -> tuple[dict[tuple[str, str], str], list[str]]:
    """Opt-outs recorded in a JSON list of {repo, unit, category, reason}, where unit globs a unit path.

    For repos whose own files should not carry audit markers; returns ({(unit key, category): reason}, stale entries).
    """
    out: dict[tuple[str, str], str] = {}
    stale = []
    for e in json.loads(path.read_text()) if path.is_file() else []:
        hits = [k for k, u in units.items() if u.repo == e["repo"] and fnmatch.fnmatch(u.path, e["unit"])]
        if not hits:
            stale.append(f"opt-out names no unit: {e['repo']}:{e['unit']} {e['category']}")
        for k in hits:
            out[(k, e["category"])] = f"opt-out ({path.name}): {e['reason']}"
    return out, stale


def apply_opt_outs(matrix: dict[str, dict[str, Cell]], recorded: dict[tuple[str, str], str]) -> list[str]:
    """Turn opted-out gap cells into opt-out with the recorded reason; return opt-outs naming no unit or category."""
    stale = []
    for (key, cat), why in recorded.items():
        cell = matrix.get(key, {}).get(cat)
        if cell is None:
            stale.append(f"opt-out names no unit/category: {key} {cat}")
        elif cell.state != "yes":
            matrix[key][cat] = Cell("opt-out", cell.evidence, why)
    return stale


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--owner", default="prosopo")
    ap.add_argument("--snap-prefix", required=True)
    ap.add_argument("--repos-file", action="append", required=True)
    ap.add_argument("--mount", action="append", default=[], help="repo:path=otherrepo (submodule mount)")
    ap.add_argument("--verify", action="store_true", help="cross-check against latest green runs via gh api")
    ap.add_argument("--api-cache", type=Path)
    ap.add_argument("--out-json", type=Path, required=True)
    ap.add_argument("--out-html", type=Path)
    ap.add_argument("--opt-outs", type=Path, default=Path(__file__).with_name("optouts.json"))
    args = ap.parse_args(argv)

    repos: list[str] = []
    for f in args.repos_file:
        repos += [x.strip() for x in Path(f).read_text().splitlines() if x.strip() and x.strip() not in repos]
    snaps = {r: Path(f"{args.snap_prefix}{r}") for r in repos}
    info = {r: json.loads((p / ".ciaudit.json").read_text()) for r, p in snaps.items() if (p / ".ciaudit.json").exists()}

    mounts: dict[str, dict[str, str]] = defaultdict(dict)
    for m in args.mount:
        repo, rest = m.split(":", 1)
        path, other = rest.split("=", 1)
        mounts[repo][path] = other
        link = snaps[repo] / path
        if link.is_dir() and not link.is_symlink() and not any(link.iterdir()):
            link.rmdir()
        if not link.exists():
            os.symlink(snaps[other].resolve(), link)

    units: dict[str, Unit] = {}
    evidence: list[Evidence] = []
    notes: list[str] = []
    for r in repos:
        found = discover(snaps[r], r, mounts.get(r))
        for u in found:
            if u.repo == r or u.key not in units:
                units[u.key] = u if u.repo == r else units.get(u.key, u)
        ctx = Ctx(snaps[r], found)
        w = Walker(ctx, r, snaps)
        wf = snaps[r] / ".github" / "workflows"
        if wf.is_dir():
            w.run_all(wf)
        evidence += w.evidence
        notes += [f"{r}: {n}" for n in w.notes] + [f"{r}: unresolved target: {x}" for x in sorted(set(ctx.unresolved))[:40]]
        print(f"event=walk repo={r} units={len(found)} evidence={len(w.evidence)} unresolved={len(set(ctx.unresolved))}", file=sys.stderr)

    checks: dict[int, Check] | None = None
    if args.verify:
        v = Verifier(gh_api_factory(args.api_cache), args.owner, {r: i["default_branch"] for r, i in info.items()})
        checks = {}
        memo: dict[object, Check] = {}
        for e in evidence:
            k = e.ref
            if k not in memo:
                try:
                    memo[k] = v.check(k)
                except Exception as exc:  # one bad API call must not sink the audit
                    memo[k] = Check("error", detail=repr(exc)[:200])
            checks[id(e)] = memo[k]
        print(f"event=verify steps={len(memo)}", file=sys.stderr)

    matrix = build_matrix(units, evidence, checks, args.owner)
    for r in repos:
        notes += [f"{r}: {n}" for n in apply_opt_outs(matrix, opt_outs(snaps[r], r))]
    recorded, stale = file_opt_outs(args.opt_outs, units)
    notes += stale + apply_opt_outs(matrix, recorded)
    report = {
        "owner": args.owner,
        "repos": {r: info.get(r, {}) for r in repos},
        "verified": checks is not None,
        "units": [
            {
                "key": k,
                "repo": u.repo,
                "path": u.path,
                "kind": u.kind,
                "name": u.name,
                "language": u.language,
                "cells": {
                    c: {
                        "state": cell.state,
                        "evidence": cell.evidence,
                        "reason": cell.reason,
                        "recommendation": recommend(u, c, cell) if cell.state in ("no", "unverified", "manual") else "",
                    }
                    for c, cell in matrix[k].items()
                },
            }
            for k, u in sorted(
                units.items(), key=lambda kv: (repos.index(kv[1].repo) if kv[1].repo in repos else 99, kv[1].kind, kv[1].path)
            )
        ],
        "notes": notes,
    }
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(report, indent=1))
    if args.out_html:
        from render import render

        args.out_html.write_text(render(report))
    gaps = sum(1 for u in report["units"] for c in u["cells"].values() if c["state"] in ("no", "unverified", "manual"))
    print(f"event=done units={len(units)} gaps={gaps}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
