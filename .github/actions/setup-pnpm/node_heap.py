"""Print NODE_OPTIONS with --max-old-space-size set to a share of total RAM.

Node's default heap (~2-4 GB) kills large builds with "JavaScript heap out of
memory", while a fixed 8192 on a 7 GB hosted runner pushes the box into the
kernel OOM killer instead. Sizing from the machine fixes both. Total, not free,
RAM: the limit caps each node process and reserves nothing, and free RAM at
setup time says little about the build that follows.

A --max-old-space-size the caller already set wins, so a job that needs a
specific value keeps it. If RAM cannot be read, NODE_OPTIONS is left as is.
"""

import argparse
import ctypes
import os
import subprocess
import sys

FLAG = "--max-old-space-size"


def total_mib_linux(meminfo: str = "/proc/meminfo") -> int:
    with open(meminfo) as f:
        for line in f:
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) // 1024
    raise ValueError("no MemTotal")


def total_mib_macos() -> int:
    out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=10, check=True).stdout
    return int(out) // 1048576


def total_mib_windows() -> int:
    class MemoryStatus(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong)] + [
            (n, ctypes.c_ulonglong)
            for n in ("total", "avail", "totalPage", "availPage", "totalVirtual", "availVirtual", "availExtended")
        ]

    status = MemoryStatus()
    status.dwLength = ctypes.sizeof(MemoryStatus)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
        raise OSError("GlobalMemoryStatusEx failed")
    return status.total // 1048576


def total_mib(platform: str = sys.platform) -> int:
    if platform.startswith("linux"):
        return total_mib_linux()
    if platform == "darwin":
        return total_mib_macos()
    if platform == "win32":
        return total_mib_windows()
    raise ValueError(f"unsupported platform {platform}")


def node_options(existing: str, total: int, percent: int) -> str:
    if FLAG in existing:
        return existing
    return f"{existing} {FLAG}={total * percent // 100}".strip()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--percent", type=int, default=75)
    a = p.parse_args(argv)
    existing = os.environ.get("NODE_OPTIONS", "")
    try:
        total = total_mib()
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        print(f"::warning::node heap left at default: cannot read RAM ({e})", file=sys.stderr)
        print(existing)
        return 0
    print(node_options(existing, total, a.percent))
    return 0


if __name__ == "__main__":
    sys.exit(main())
