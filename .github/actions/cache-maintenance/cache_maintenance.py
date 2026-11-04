"""Evict stale entries from a self-hosted runner's local tool caches.

Each target is `path:depth`: the entries considered for eviction are the files
or directories exactly `depth` levels below `path`, and an entry is only ever
removed whole. Depth matters because a half-deleted entry can be worse than a
missing one: cargo trusts an extracted crate under registry/src once its
`.cargo-ok` marker exists, so that tree is evicted one crate directory at a time.

An entry's last use is the newest atime/mtime of anything inside it. relatime
(the Linux default) still advances atime once a day, which is the granularity
the age limit works at.

Runs at most once per `interval` (a stamp file), because walking a pnpm store
of a few million files costs tens of seconds and every job calls this.

No locking: each runner VM is single-tenant (https://github.com/prosopo/gh-runner),
so nothing else touches these caches while a job runs.
"""

import argparse
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path

GIB = 1024**3
DEFAULT_TARGETS = """
~/.pnpm-store:4
~/.cargo/registry/cache:2
~/.cargo/registry/src:2
~/.cargo/git/checkouts:1
~/.cargo/git/db:1
~/.cache/ms-playwright:1
~/.cache/turbo:1
"""


@dataclass
class Entry:
    path: Path
    last_used: float
    size: int


def log(event: str, **fields: object) -> None:
    print(json.dumps({"event": event, **fields}, default=str), flush=True)


def parse_target(spec: str) -> tuple[Path, int]:
    path, _, depth = spec.strip().rpartition(":")
    if not path or not depth.isdigit():
        return Path(os.path.expanduser(spec.strip())), 1
    return Path(os.path.expanduser(path)), int(depth)


def measure(path: Path) -> tuple[float, int]:
    """Newest atime/mtime and total size of a file or tree, never following symlinks.

    Directory times are ignored: this walk itself bumps their atime.
    """
    st = path.lstat()
    newest, size = max(st.st_atime, st.st_mtime), st.st_size
    if path.is_dir() and not path.is_symlink():
        newest = 0.0
        for root, _dirs, files in os.walk(path):
            for name in files:
                try:
                    s = os.lstat(os.path.join(root, name))
                except FileNotFoundError:
                    continue
                newest = max(newest, s.st_atime, s.st_mtime)
                size += s.st_size
        newest = newest or st.st_mtime
    return newest, size


def entries_at(root: Path, depth: int) -> list[Entry]:
    level = [root] if root.is_dir() else []
    for _ in range(depth):
        level = [c for d in level if d.is_dir() and not d.is_symlink() for c in d.iterdir()]
    found = []
    for p in level:
        try:
            last_used, size = measure(p)
        except FileNotFoundError:
            continue
        found.append(Entry(p, last_used, size))
    return found


def remove(entry: Entry) -> None:
    if entry.path.is_dir() and not entry.path.is_symlink():
        shutil.rmtree(entry.path, ignore_errors=True)
    else:
        entry.path.unlink(missing_ok=True)


def free_bytes(path: Path) -> int:
    probe = path
    while not probe.exists():
        probe = probe.parent
    return shutil.disk_usage(probe).free


def plan(entries: list[Entry], now: float, max_age_days: float, max_bytes: int,
         min_free: int, free_now: int) -> list[Entry]:
    """Entries to delete: everything past max age, then oldest-first until both limits hold."""
    cutoff = now - max_age_days * 86400
    doomed = [e for e in entries if e.last_used < cutoff]
    kept = sorted((e for e in entries if e.last_used >= cutoff), key=lambda e: e.last_used)
    total = sum(e.size for e in kept)
    free = free_now + sum(e.size for e in doomed)
    while kept and (total > max_bytes or free < min_free):
        e = kept.pop(0)
        doomed.append(e)
        total -= e.size
        free += e.size
    return doomed


def due(stamp: Path, interval_minutes: float, now: float) -> bool:
    try:
        return now - stamp.stat().st_mtime >= interval_minutes * 60
    except FileNotFoundError:
        return True


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--targets", default="", help="newline-separated path:depth list; empty means DEFAULT_TARGETS")
    p.add_argument("--max-age-days", type=float, default=14)
    p.add_argument("--max-size-gb", type=float, default=60)
    p.add_argument("--min-free-gb", type=float, default=20)
    p.add_argument("--interval-minutes", type=float, default=60)
    p.add_argument("--stamp", default="~/.cache/prosopo-cache-maintenance.stamp")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args(argv)

    now = time.time()
    stamp = Path(os.path.expanduser(a.stamp))
    if not due(stamp, a.interval_minutes, now):
        log("cache_maintenance.skip", reason="ran recently", stamp=stamp)
        return 0

    targets = [parse_target(t) for t in (a.targets or DEFAULT_TARGETS).splitlines() if t.strip()]
    entries = [e for root, depth in targets for e in entries_at(root, depth)]
    anchor = targets[0][0] if targets else Path.home()
    doomed = plan(entries, now, a.max_age_days, int(a.max_size_gb * GIB),
                  int(a.min_free_gb * GIB), free_bytes(anchor))
    freed = sum(e.size for e in doomed)
    for e in doomed:
        if not a.dry_run:
            remove(e)
    log("cache_maintenance.done", entries=len(entries), evicted=len(doomed),
        freed_gb=round(freed / GIB, 2), total_gb=round(sum(e.size for e in entries) / GIB, 2),
        dry_run=a.dry_run)
    if not a.dry_run:
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.touch()
    return 0


if __name__ == "__main__":
    sys.exit(main())
