"""Download default-branch snapshots of repos via the GitHub API tarball endpoint.

Tarballs instead of clones: no git history is needed, only the tree at HEAD of
the default branch, and the snapshot records which commit was audited.
"""

from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
import tarfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def gh_api(path: str, timeout: int = 300) -> bytes:
    proc = subprocess.run(["gh", "api", path], capture_output=True, timeout=timeout, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"gh api {path} failed: {proc.stderr.decode()[:300]}")
    return proc.stdout


def extract_tarball(data: bytes, dest: Path) -> None:
    """Extract a GitHub tarball, dropping its single `<owner>-<repo>-<sha>/` prefix."""
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        members = []
        for m in tar.getmembers():
            parts = m.name.split("/", 1)
            if len(parts) < 2 or not parts[1]:
                continue
            m.name = parts[1]
            if m.issym() or m.islnk():
                continue  # committed absolute symlinks (e.g. a venv) are rejected by filter="data"
            members.append(m)
        tar.extractall(dest, members=members, filter="data")


def fetch(owner: str, repo: str, dest: Path) -> dict[str, str]:
    meta = json.loads(gh_api(f"repos/{owner}/{repo}"))
    branch = meta["default_branch"]
    sha = json.loads(gh_api(f"repos/{owner}/{repo}/commits/{branch}"))["sha"]
    extract_tarball(gh_api(f"repos/{owner}/{repo}/tarball/{sha}"), dest)
    info = {"owner": owner, "repo": repo, "default_branch": branch, "sha": sha}
    (dest / ".ciaudit.json").write_text(json.dumps(info))
    return info


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--owner", default="prosopo")
    ap.add_argument("--repos", nargs="*", default=[])
    ap.add_argument("--repos-file", action="append", default=[], help="one repo per line")
    ap.add_argument("--dest-prefix", required=True, help="each repo goes to <prefix><repo>")
    args = ap.parse_args(argv)
    for f in args.repos_file:
        args.repos += [ln.strip() for ln in Path(f).read_text().splitlines() if ln.strip()]

    def one(repo: str) -> str:
        try:
            info = fetch(args.owner, repo, Path(f"{args.dest_prefix}{repo}"))
            return f"event=fetch.ok repo={repo} sha={info['sha'][:10]}"
        except Exception as exc:  # report and continue with the other repos
            return f"event=fetch.fail repo={repo} error={exc!r}"

    with ThreadPoolExecutor(max_workers=6) as pool:
        lines = list(pool.map(one, args.repos))
    print("\n".join(lines))
    return 1 if any("fetch.fail" in line for line in lines) else 0


if __name__ == "__main__":
    sys.exit(main())
