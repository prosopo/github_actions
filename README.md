# github_actions
github actions reusable modules for Prosopo

## Available Actions

### npm
Sets up Node.js environment and manages npm dependencies. Configures Node.js version from `package.json`, optionally restores npm cache, installs a specific npm version, and runs `npm ci` to install project dependencies.

### restore_npm_cache
Restores npm, Cypress, Turbo, and Nx caches from GitHub Actions cache storage. Uses a wildcard cache key to match the most recent compatible cache. Skips restoration on self-hosted runners for performance reasons.

### save_npm_cache
Saves npm, Cypress, Turbo, and Nx caches to GitHub Actions cache storage. Verifies npm cache integrity before saving and automatically cleans up old caches, keeping only the latest one to manage storage usage.

### restore_cargo_cache
Restores Rust/Cargo registry, git cache, and build artifacts from GitHub Actions cache storage. Uses a wildcard cache key to match the most recent compatible cache. Skips restoration on self-hosted runners for performance reasons.

### save_cargo_cache
Saves Rust/Cargo registry, git cache, and build artifacts to GitHub Actions cache storage. Automatically cleans up old caches, keeping only the latest one to manage storage usage.

### print_contexts
Utility action for debugging that prints various GitHub Actions contexts (github, env, vars, job, runner, secrets, strategy, matrix, needs, inputs, steps) as JSON output in collapsible groups.

### rust-toolchain
Automatically installs the Rust toolchain based on your project's `rust-toolchain.toml` configuration. Extracts the toolchain channel, targets, and components from the config file and applies them using the dtolnay/rust-toolchain action.

### setup-pnpm
Installs node (`actions/setup-node`) and pnpm (`pnpm/action-setup`, version from `packageManager` unless given), restores the pnpm store cache under `pnpm-store-<os>-<arch>-<lockfile hash>` (hosted runners only by default) and runs `pnpm install --frozen-lockfile`. Every step is an input with a default: `node-version`/`node-version-file`, `pnpm-version`, `store-path`, `install-args`, `run-install`, `working-directory`.

### restore-cache / save-cache
Generic pair over `actions/cache/restore` and `actions/cache/save` using the key `<name>-<os>-<arch>-<hashFiles(key-files)><suffix>`; restore falls back to the newest `<name>-<os>-<arch>-` entry. Both skip self-hosted runners unless told otherwise.

### app-token
Mints a GitHub App installation token via `peter-murray/workflow-application-token-action`. Pass the app id and private key (e.g. `vars.PROSOPONATOR_APP_ID`, `secrets.PROSOPONATOR_APP_PRIVATE_KEY`); the token is in `outputs.token`.

### cache-maintenance
On self-hosted runner VMs (https://github.com/prosopo/gh-runner), evicts local cache entries (pnpm store, cargo, Playwright, turbo) unused for `max-age-days`, then oldest-first until under `max-size-gb` and above `min-free-gb`. Runs at most once per `interval-minutes`. setup-pnpm and rust-toolchain run it first; no-op on GitHub-hosted runners.

### check-timeouts
Fails when any job or step in `.github/workflows` lacks `timeout-minutes`.

### workflow-lint
Fails when a workflow in `.github/workflows` breaks a house rule that actionlint does not check: missing concurrency (or a `workflow_call` group not keyed on a `concurrency-key` input), no `defaults.run.shell`, `pull_request` without the five PR types or a root job without a draft skip, `pull_request_target` without a fork guard, missing or write-granting top-level permissions, `cargo --allow-dirty`, a hardcoded `main` ref outside `on:`, or a secret written to a file or `GITHUB_ENV`. Waive one finding with `# workflow-lint: allow <rule> -- <reason>` on its line or the line above; a waiver without a reason fails.

### tag-versions
On push to main, tags each package whose `package.json` version changed since `before` as `<unscoped name>-v<version>`; a changesets `fixed` group gets one tag named after its first entry. Existing tags are skipped and a push with no usable `before` tags nothing. Check out with `fetch-depth: 0` and an app token so the tag push triggers publish workflows. Created tags are in `outputs.tags` (JSON).

### detect-languages
Outputs `has-<lang>` and `<lang>-changed` for js, rust, python and docker (`.github/actions/detect-languages/detect_languages.py`). A language changed when the diff from `base` touches its files; edits under `.github/`, `pnpm-lock.yaml`, `.nvmrc` or `turbo.json` count as changing every present language. No usable `base` (first push, all-zero SHA) means every present language changed.

## Reusable workflows

Each takes `actions-ref`: pass the same SHA the `uses:` line pins, so the workflow runs this repo's actions from that commit.

- `cache-main.yml`: on push to main and cron, restores every main cache for real (keeps it from 7-day eviction) and rebuilds and saves any miss.
- `prime-runners.yml`: one job per online self-hosted runner VM (`vm-*` label, `host-*` fallback) filling its local pnpm store, cargo registry, Playwright and turbo build of main. Needs a token that can list org runners.
- `cancel-runs-on-pr-close.yml`: on PR close, cancels its in-flight runs and deletes its cache entries.
- Stage workflows, one per CI stage, usable unchanged by captcha, captcha-private and Protect. Fork PRs and drafts are skipped.
  - `static.yml` / `test.yml`: a `changes` job (detect-languages), then js, rust and python jobs, each running only when the repo has that language and the change touched it. Default commands are the turbo tasks every repo exposes (`lint`, `lint:rust`, `lint:py` / `test`, `test:rust`, `test:py`); override with `js-command` etc.
  - `build.yml`: one `turbo run build`, uploading the turbo cache as an artifact for later stages.
  - `e2e.yml`: restores that cache, starts only the services in the repo's own compose file (none if absent), runs `test:e2e`.
  - `security.yml`: `pnpm audit` (JS repos), `cargo audit` (Rust repos), gitleaks.
  - `pr-housekeeping.yml`: requires a `.changeset/*.md` when `packages/` changed (repos with changesets), rejects WIP titles.
  - `stage-workflows-selftest.yml` runs static and test against `.github/fixtures/js-only`: js must pass, rust and python must be skipped.
- `auto-update-pr.yml`: updates one random behind, mergeable, auto-merge PR at a time (`.github/scripts/auto_update_pr.py`).

## This repo's own CI

- `lint.yml` (`check`, required): actionlint, yamllint, taplo, shellcheck of composite action shell, `.github/scripts/validate_actions.py` (every action.yml's metadata: required keys, input/output descriptions, a `shell` on each composite `run` step, which actionlint does not read), the Python unit tests and the timeout check.
- `actions-selftest.yml` (`actions-test`, required): runs restore-cache/save-cache (a miss, a save, then an exact hit in a second job) and setup-pnpm (against `tests/fixtures/pnpm`) on ubuntu and macOS, as consumers call them. `actions-test` fails unless every test job succeeded, and on drafts.

## Cache rules (restore-cache / save-cache)

- Dependency caches are keyed on lockfiles; build outputs on hashFiles of their inputs, with prefix fallback.
- Main saves; PRs never save dependency stores, and keep one build entry per name (older ones deleted).
- No save after an exact hit or when the restored tree is unchanged (`fingerprint`).
