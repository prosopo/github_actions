"""Print a cheap fingerprint of a set of cache paths.

Hashes each file's relative path, size and mtime rather than its bytes: a
cargo target dir runs to tens of GB, and this only has to tell "the build
wrote something" from "the build changed nothing" so save-cache can skip a
duplicate upload.
"""

import argparse
import hashlib
import os
import sys


def fingerprint(paths: list[str]) -> str:
    h = hashlib.sha256()
    for raw in paths:
        root = os.path.expanduser(raw.strip())
        h.update(f"root\0{raw.strip()}\n".encode())
        if not os.path.exists(root):
            continue
        walked = [(root, [], [""])] if os.path.isfile(root) else os.walk(root)
        rows = []
        for d, _dirs, files in walked:
            for f in files:
                full = os.path.join(d, f) if f else d
                try:
                    st = os.lstat(full)
                except FileNotFoundError:
                    continue
                rows.append(f"{os.path.relpath(full, root)}\0{st.st_size}\0{st.st_mtime_ns}\n")
        for row in sorted(rows):
            h.update(row.encode())
    return h.hexdigest()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--paths", required=True, help="newline-separated paths")
    a = p.parse_args(argv)
    print(fingerprint([x for x in a.paths.splitlines() if x.strip()]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
