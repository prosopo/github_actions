"""Print rust-toolchain.toml's channel, targets and components as GITHUB_OUTPUT lines.

A real TOML parse, not grep: a file without a targets (or components) line is
valid and common, and must yield an empty list rather than fail the step.
"""

import argparse
import json
import sys
import tomllib
from pathlib import Path


def read(path: Path) -> dict[str, str]:
    toolchain = tomllib.loads(path.read_text()).get("toolchain", {})
    channel = toolchain.get("channel")
    if not channel:
        raise ValueError(f"{path} has no [toolchain] channel")
    return {
        "toolchain": channel,
        "targets": ",".join(toolchain.get("targets", [])),
        "components": ",".join(toolchain.get("components", [])),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--file", default="rust-toolchain.toml")
    a = p.parse_args(argv)
    values = read(Path(a.file))
    print(json.dumps({"event": "rust-toolchain", **values}), file=sys.stderr)
    for key, value in values.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
