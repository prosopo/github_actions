"""Report which languages a repo has and which a change touched.

Stage workflows skip a language's jobs when the repo has none of it, or when
the change did not touch it, so one workflow file serves every repo. A
language is present when any tracked file matches its patterns; it changed
when any file in the diff does. Workflow and lockfile edits count as changing
every present language, since they can break any of them.
"""

import argparse
import fnmatch
import json
import os
import subprocess
import sys

LANGUAGES: dict[str, list[str]] = {
    "js": ["*.ts", "*.tsx", "*.js", "*.mjs", "*.cjs", "*.jsx", "package.json"],
    "rust": ["*.rs", "Cargo.toml", "Cargo.lock"],
    "python": ["*.py", "pyproject.toml", "uv.lock"],
    "go": ["*.go", "go.mod", "go.sum"],
    "php": ["*.php", "composer.json", "composer.lock"],
    "docker": ["Dockerfile", "*.Dockerfile", "*.dockerfile", "docker-compose*.yml"],
}

AFFECTS_ALL = [".github/*", "pnpm-lock.yaml", "package-lock.json", ".nvmrc", "turbo.json"]

# Root lockfile -> how the JS jobs install; only pnpm repos get the default turbo commands.
LOCKFILES = {"pnpm-lock.yaml": "pnpm", "package-lock.json": "npm"}


def _matches(path: str, patterns: list[str]) -> bool:
    name = path.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatch(name, p) or fnmatch.fnmatch(path, p) for p in patterns)


def package_manager(tracked: list[str]) -> str:
    """pnpm wins when both lockfiles exist; empty when the repo has neither."""
    present = [pm for lockfile, pm in LOCKFILES.items() if lockfile in tracked]
    return present[0] if present else ""


def detect(tracked: list[str], changed: list[str] | None) -> dict[str, bool]:
    """`changed` None means unknown (e.g. first push): treat everything as changed."""
    everything = changed is None or any(_matches(p, AFFECTS_ALL) for p in changed)
    out: dict[str, bool] = {}
    for lang, patterns in LANGUAGES.items():
        has = any(_matches(p, patterns) for p in tracked)
        out[f"has-{lang}"] = has
        out[f"{lang}-changed"] = has and (
            everything or any(_matches(p, patterns) for p in changed or [])
        )
    return out


def _git(*args: str) -> list[str]:
    out = subprocess.run(["git", *args], capture_output=True, text=True, timeout=60, check=True)
    return [line for line in out.stdout.splitlines() if line]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default="", help="commit to diff against; empty or all-zero = unknown")
    args = parser.parse_args(argv)

    changed = None
    if args.base and set(args.base) != {"0"}:
        try:
            changed = _git("diff", "--name-only", f"{args.base}...HEAD")
        except subprocess.CalledProcessError:
            changed = None
    tracked = _git("ls-files")
    result: dict[str, bool | str] = {**detect(tracked, changed), "package-manager": package_manager(tracked)}
    print(json.dumps({"event": "detect-languages", **result}))
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a") as f:
            for key, value in result.items():
                f.write(f"{key}={value if isinstance(value, str) else str(value).lower()}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
