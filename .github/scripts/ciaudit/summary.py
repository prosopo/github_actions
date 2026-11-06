"""Print a compact text matrix of an audit report (for terminal review)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from units import CATEGORIES

SYM = {"yes": "Y", "unverified": "?", "manual": "M", "no": ".", "n/a": "-", "opt-out": "O"}


def lines(report: dict, repo: str | None = None, kind: str | None = None) -> list[str]:
    out = []
    for u in report["units"]:
        if (repo and u["repo"] != repo) or (kind and u["kind"] != kind):
            continue
        cells = "".join(SYM[u["cells"][c]["state"]] for c in CATEGORIES)
        out.append(f"{cells} {u['repo']}:{u['kind']}:{u['path']} ({u['name']})")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--report", type=Path, required=True)
    ap.add_argument("--repo")
    ap.add_argument("--kind")
    a = ap.parse_args(argv)
    print("\n".join(lines(json.loads(a.report.read_text()), a.repo, a.kind)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
