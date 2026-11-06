"""Print workflow contexts and identity probes in collapsed log groups.

Contexts arrive as JSON in <NAME>_CONTEXT env vars. Secrets and env print as
names only, and every secret value is registered with add-mask first, so a
value that leaks into a probe's output (or a derived form GitHub would not
mask on its own) still shows as ***. github.token is dropped for the same
reason. A probe that is missing, fails or hangs prints its error and moves on:
diagnostics must never fail the job they are diagnosing.
"""

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

CONTEXTS = ["github", "env", "vars", "secrets", "job", "runner", "strategy", "matrix", "needs", "inputs", "steps"]
NAMES_ONLY = {"secrets", "env"}
PROBE_TIMEOUT = 20

Run = Callable[[list[str]], str]


def run(cmd: list[str]) -> str:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=PROBE_TIMEOUT, stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        return f"{cmd[0]}: not installed"
    except subprocess.TimeoutExpired:
        return f"{' '.join(cmd)}: timed out after {PROBE_TIMEOUT}s"
    out = (p.stdout + p.stderr).strip()
    return out if p.returncode == 0 else f"{' '.join(cmd)}: exit {p.returncode}: {out}"


def docker_logins(config: Path) -> str:
    try:
        auths = json.loads(config.read_text()).get("auths", {})
    except (OSError, ValueError):
        return f"no docker config at {config}"
    return "\n".join(sorted(auths)) or "no registry logins"


PROBES: dict[str, Callable[[Run], list[tuple[str, str]]]] = {
    "tools": lambda r: [(c[0], r(c)) for c in (["node", "--version"], ["pnpm", "--version"], ["docker", "--version"])],
    "npm": lambda r: [("npm user", r(["npm", "whoami"])), ("npm registry", r(["npm", "config", "get", "registry"]))],
    "docker": lambda r: [("docker logins", docker_logins(Path(os.environ.get("DOCKER_CONFIG", Path.home() / ".docker")) / "config.json"))],
    "aws": lambda r: [("aws identity", r(["aws", "sts", "get-caller-identity", "--output", "json"]))],
    "gh": lambda r: [("gh user", r(["gh", "api", "user", "--jq", ".login"]))],
}


def parse(raw: str | None) -> object:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def secret_values(secrets: object) -> list[str]:
    if not isinstance(secrets, dict):
        return []
    lines = {line.strip() for v in secrets.values() if isinstance(v, str) for line in v.splitlines()}
    return sorted(v for v in lines if v)


def group(name: str, body: str) -> list[str]:
    return [f"::group::{name}", body, "::endgroup::"]


def render(environ: dict[str, str], probes: list[str], runner: Run = run) -> list[str]:
    contexts = {name: parse(environ.get(f"{name.upper()}_CONTEXT")) for name in CONTEXTS}
    out = [f"::add-mask::{v}" for v in secret_values(contexts["secrets"])]
    github = contexts["github"]
    if isinstance(github, dict):
        github.pop("token", None)
    for name, value in contexts.items():
        if value is None:
            continue
        if name in NAMES_ONLY and isinstance(value, dict):
            value = sorted(value)
        out += group(name, json.dumps(value, indent=2, sort_keys=True))
    for probe in probes:
        if probe not in PROBES:
            out += group(probe, f"unknown probe; known: {', '.join(PROBES)}")
            continue
        out += group(probe, "\n".join(f"{label}: {result}" for label, result in PROBES[probe](runner)))
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--probes", default="tools", help="Comma-separated: " + ", ".join(PROBES))
    a = p.parse_args(argv)
    probes = [s.strip() for s in a.probes.split(",") if s.strip()]
    print("\n".join(render(dict(os.environ), probes)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
