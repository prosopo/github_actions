#!/usr/bin/env bash
# Puts the linters `pnpm run lint` needs into ~/.local/bin (on PATH on hosted
# runners), at pinned versions so a new upstream release cannot break CI.
# Skips any tool already present, so it is safe to run locally.
set -euo pipefail

ACTIONLINT_VERSION=1.7.12
TAPLO_VERSION=0.10.0
BIN="${HOME}/.local/bin"
mkdir -p "$BIN"

if ! command -v actionlint >/dev/null; then
  timeout 120 curl -fsSL "https://github.com/rhysd/actionlint/releases/download/v${ACTIONLINT_VERSION}/actionlint_${ACTIONLINT_VERSION}_linux_amd64.tar.gz" \
    | tar -xz -C "$BIN" actionlint
fi
if ! command -v taplo >/dev/null; then
  # Not the "-full" asset: that variant is no longer published.
  timeout 120 curl -fsSL "https://github.com/tamasfe/taplo/releases/download/${TAPLO_VERSION}/taplo-linux-x86_64.gz" | gunzip > "$BIN/taplo"
  chmod +x "$BIN/taplo"
fi
command -v yamllint >/dev/null || timeout 180 pipx install yamllint
command -v shellcheck >/dev/null || { sudo timeout 240 apt-get update -qq && sudo timeout 240 apt-get install -y -qq shellcheck; }
python3 -c "import yaml" 2>/dev/null || timeout 120 python3 -m pip install --quiet --user --break-system-packages pyyaml
