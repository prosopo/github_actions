#!/usr/bin/env bash
# Reclaim disk on a self-hosted GitHub Actions runner.
#
# The fleet is non-ephemeral: every job leaves its build state behind, and
# nothing ever removes it. On a Rust repo that accumulates fast -- a single
# Protect checkout is ~17 GB of target/, over half of it incremental state that
# is worthless the moment the job ends -- so a 60 GB runner fills after a
# handful of branches and then fails jobs late and confusingly with ENOSPC.
#
# Age-based throughout, never "delete everything": jobs run back to back on
# these hosts and share ~/.cargo, so anything a *running* job might be holding
# has to survive. MAX_AGE_DAYS is the guard -- state that has not been touched
# in that long cannot belong to an in-flight job.
#
# Two entry points, same script:
#   --scheduled   nightly timer; prunes anything older than MAX_AGE_DAYS
#   --if-low      no-op unless free space is under MIN_FREE_GB, then prunes
#                 with a shorter age cut. Called from the runner's
#                 ACTIONS_RUNNER_HOOK_JOB_STARTED hook, i.e. before a job runs
#                 rather than after it has already half-filled the disk.
set -uo pipefail

RUNNER_HOME="${RUNNER_HOME:-/opt/actions-runner}"
WORK_DIR="${RUNNER_WORK_DIR:-$RUNNER_HOME/_work}"
CARGO_HOME="${CARGO_HOME:-$RUNNER_HOME/.cargo}"
MIN_FREE_GB="${MIN_FREE_GB:-15}"
MAX_AGE_DAYS="${MAX_AGE_DAYS:-7}"
# Under pressure the same sweep runs with a tighter cut. Still far longer than
# any single job, so it cannot race one.
LOW_DISK_AGE_DAYS="${LOW_DISK_AGE_DAYS:-1}"

free_gb() { df -BG --output=avail "$WORK_DIR" | tail -n1 | tr -dc '0-9'; }
log() { echo "[runner-disk-clean] $*"; }

sweep() {
  local age="$1"
  log "sweeping state older than ${age}d (free: $(free_gb) GB)"

  # Incremental compilation state: per-working-copy, never reused across jobs.
  # Biggest single win and the safest thing here.
  find "$WORK_DIR" -type d -name incremental -path '*/target/*' -mtime "+${age}" \
    -prune -exec rm -rf {} + 2>/dev/null

  # Whole target/ dirs from branches nobody has built since. A later job on that
  # branch just recompiles; a stale tree costs GB indefinitely.
  find "$WORK_DIR" -mindepth 2 -maxdepth 4 -type d -name target -mtime "+${age}" \
    -prune -exec rm -rf {} + 2>/dev/null

  # Workspace checkouts for repos the fleet no longer builds. actions/checkout
  # re-clones; keeping them buys nothing once they are cold.
  find "$WORK_DIR" -mindepth 1 -maxdepth 1 -type d -mtime "+$((age * 4))" \
    -exec rm -rf {} + 2>/dev/null

  # Extracted crate sources are regenerated from the .crate archives beside
  # them, so this is cache, not state. registry/cache and git/db are left alone:
  # deleting those means re-downloading on the next job.
  find "$CARGO_HOME/registry/src" -mindepth 2 -maxdepth 2 -type d -mtime "+${age}" \
    -exec rm -rf {} + 2>/dev/null

  # node_modules trees under cold checkouts, npm's own cache, and dangling
  # docker layers. `docker system prune` without --all deliberately: images a
  # running job pulled stay, only unreferenced layers and stopped containers go.
  find "$WORK_DIR" -type d -name node_modules -mtime "+${age}" \
    -prune -exec rm -rf {} + 2>/dev/null
  npm cache verify >/dev/null 2>&1
  command -v docker >/dev/null && docker system prune --force --filter "until=${age}d" >/dev/null 2>&1

  log "done (free: $(free_gb) GB)"
}

case "${1:---scheduled}" in
  --scheduled)
    sweep "$MAX_AGE_DAYS"
    ;;
  --if-low)
    avail="$(free_gb)"
    if [ "${avail:-0}" -ge "$MIN_FREE_GB" ]; then
      log "free ${avail} GB >= ${MIN_FREE_GB} GB threshold, nothing to do"
      exit 0
    fi
    log "free ${avail} GB below ${MIN_FREE_GB} GB threshold"
    sweep "$LOW_DISK_AGE_DAYS"
    # Deliberately does not fail the job when still short: a job that might have
    # squeaked through should not be pre-emptively killed. The log line is the
    # signal that the fleet needs a bigger disk or fewer repos.
    [ "$(free_gb)" -lt "$MIN_FREE_GB" ] && log "WARNING: still under threshold after sweep"
    ;;
  *)
    echo "usage: $0 [--scheduled|--if-low]" >&2
    exit 2
    ;;
esac
exit 0
