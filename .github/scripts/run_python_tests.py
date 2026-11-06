"""Run every directory's test_*.py under --root with unittest, one directory at a time.

Each action keeps its tests beside its script and imports it as a top-level
module, so each directory is its own unittest start dir: one `discover` over
the whole tree would put the wrong directory on sys.path. Directories are
found by glob, so a new action's tests run without registering them anywhere.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path


def test_dirs(root: Path) -> list[Path]:
    return sorted({p.parent for p in root.rglob("test_*.py") if "fixtures" not in p.parts})


def run(root: Path, timeout: int) -> int:
    dirs = test_dirs(root)
    failed = []
    for d in dirs:
        cmd = [sys.executable, "-m", "unittest", "discover", "-q", "-s", str(d), "-t", str(d), "-p", "test_*.py"]
        code = subprocess.run(cmd, timeout=timeout, check=False).returncode
        print(json.dumps({"event": "python-tests", "dir": str(d), "exit": code}), flush=True)
        if code != 0:
            failed.append(str(d))
    print(json.dumps({"event": "python-tests-summary", "dirs": len(dirs), "failed": failed}), flush=True)
    if not dirs:
        return 1
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", default=".github")
    p.add_argument("--timeout", type=int, default=120, help="seconds per directory")
    args = p.parse_args(argv)
    return run(Path(args.root), args.timeout)


if __name__ == "__main__":
    sys.exit(main())
