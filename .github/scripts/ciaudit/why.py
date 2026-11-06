"""Print the evidence behind one unit's cells (debug aid)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--report", type=Path, required=True)
    ap.add_argument("--key", required=True, help="substring of repo:kind:path")
    ap.add_argument("--cat")
    ap.add_argument("--limit", type=int, default=4)
    a = ap.parse_args(argv)
    for u in json.loads(a.report.read_text())["units"]:
        if a.key not in u["key"]:
            continue
        print("##", u["key"])
        for c, cell in u["cells"].items():
            if a.cat and c != a.cat:
                continue
            print(f"  {c}: {cell['state']} {cell.get('recommendation', '')[:100]}")
            for e in cell["evidence"][: a.limit]:
                step, detail = e["step"][:60], e.get("detail", "")[:60]
                print(f"     {e['workflow']}:{e['job']} | {step} | {e.get('status', '')} {detail} | {e['via'][:230]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
