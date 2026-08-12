#!/usr/bin/env bash
# Point ACTIONS_RUNNER_HOOK_JOB_STARTED at this file (via the runner's .env) to
# have every job check disk headroom before it starts.
#
# Before the job rather than after: a job that fails on ENOSPC has already burnt
# its full build time, and the failure surfaces as something unrelated-looking
# deep in a linker (`ar: ... No space left on device`).
#
# Never fails the job -- see the note at the end of runner-disk-clean.sh.
set -uo pipefail
exec "$(dirname "$0")/runner-disk-clean.sh" --if-low
