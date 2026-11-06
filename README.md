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
Fails when any job or step in `.github/workflows` lacks `timeout-minutes`, or when a hang-prone command (pnpm, npm, docker, cargo, gh, curl, apt-get, `git fetch`/`push`/`clone`, ...) in a workflow or composite action (`.github/actions`) runs without `timeout N`. A composite step cannot set `timeout-minutes`, and a step timeout over a multi-command block does not say which command hung. curl/wget with `--max-time` pass; waive a line with `# no-timeout: <why>`. Keep each command's limit under its step's.

### timeout-shim
A Perl `timeout DURATION CMD...` for macOS runners, which lack coreutils: on expiry it sends TERM to the command's process group, KILL 5s later, and exits 124. Actions that wrap commands in `timeout` put it on `PATH` in their first step when `timeout` is missing.

### workflow-lint
Fails when a workflow in `.github/workflows` breaks a house rule that actionlint does not check: missing concurrency (or a `workflow_call` group not keyed on a `concurrency-key` input), no `defaults.run.shell`, `pull_request` without the five PR types or a root job without a draft skip, any `pull_request_target` trigger (cannot be waived), missing or write-granting top-level permissions, a literal step `with.node-version` (use `node-version-file`), `cargo --allow-dirty`, a hardcoded `main` ref outside `on:`, or a secret written to a file or `GITHUB_ENV`. Waive one finding with `# workflow-lint: allow <rule> -- <reason>` on its line or the line above; a waiver without a reason fails.

### ansible-vault-check
Fails when an ansible inventory holds a sensitive value in plaintext (`.github/actions/ansible-vault-check/vault_check.py`). Every leaf key under `<inventory-dir>/<env>/{group,host}_vars/**/*.yml` must be listed in the `classification` file under exactly one of `sensitive` (must be an inline `!vault` block), `plain`, or `pending_encryption` (a known plaintext secret: warns, does not fail, so a repo can adopt the check before rotating it). A whole-file vault, a non-mapping file or an unclassified key fails. `allow-references: "true"` also accepts a sensitive value that is a Jinja `{{ }}` or `${}` reference. Reads `!vault` tags without decrypting, so it needs no vault password and runs on drafts and untrusted PRs. Inputs: `inventory-dir`, `classification`, `envs` (space-separated; default every subdirectory).

### tag-versions
On push to main, tags each package whose `package.json` version changed since `before` as `<unscoped name>-v<version>`; a changesets `fixed` group gets one tag named after its first entry. Existing tags are skipped and a push with no usable `before` tags nothing. Check out with `fetch-depth: 0` and an app token so the tag push triggers publish workflows. Created tags are in `outputs.tags` (JSON).

### container-logs
Prints `docker ps -a` and the last `lines` (default 200) of every container's log, one group each, looping `docker ps -aq` so testcontainers and `docker run` containers are covered, not just compose. Add it as the last step of any job that starts containers, with `if: ${{ failure() || cancelled() }}`. Console only (Actions masks secrets there); never upload the logs as artifacts. Never fails the job.

### detect-languages
Outputs `has-<lang>` and `<lang>-changed` for js, rust, python, go, php and docker, and `package-manager` (`pnpm` for a root `pnpm-lock.yaml`, else `npm` for a root `package-lock.json`, else empty) (`.github/actions/detect-languages/detect_languages.py`). A language changed when the diff from `base` touches its files; edits under `.github/`, `pnpm-lock.yaml`, `package-lock.json`, `.nvmrc` or `turbo.json` count as changing every present language. No usable `base` (first push, all-zero SHA) means every present language changed. `exclude` lists directories to ignore, such as test fixtures in languages the repo itself does not use.

## Reusable workflows

Each takes `actions-ref`: pass the same SHA the `uses:` line pins, so the workflow runs this repo's actions from that commit.

- `cache-main.yml`: on push to main and cron, restores every main cache for real (keeps it from 7-day eviction) and rebuilds and saves any miss.
- `prime-runners.yml`: one job per online self-hosted runner VM (`vm-*` label, `host-*` fallback) filling its local pnpm store, cargo registry, Playwright and turbo build of main. Needs a token that can list org runners.
- `cancel-runs-on-pr-close.yml`: on PR close, cancels its in-flight runs and deletes its cache entries.
- `dependabot-to-draft.yml`: converts a Dependabot PR to draft when it opens (with a comment saying why), so CI waits until someone marks it ready; the `keep-ready` label opts out. Call it on `pull_request: types: [opened, reopened]` with `pull-requests: write` and pass `secrets: token: ${{ secrets.PROSOPONATOR_PAT }}` (a Dependabot secret). Without a token it falls back to `GITHUB_TOKEN`, which still drafts the PR, but the `converted_to_draft` event then starts no runs, so in-flight suites are not cancelled.
- Stage workflows, one per CI stage, usable unchanged by captcha, captcha-private and Protect. Fork PRs and drafts are skipped.
  - `static.yml` / `test.yml`: a `changes` job (detect-languages), then js, rust, python, go and php jobs, each running only when the repo has that language and the change touched it. pnpm is set up only in pnpm repos, and npm repos get `npm ci` in the js job, so npm, Go, PHP and Python-only repos need no pnpm. Default commands are the turbo tasks every pnpm repo exposes (`lint`, `lint:rust`, `lint:py` / `test`, `test:rust`, `test:py`), `go vet`/`go test` and `composer run-script lint`/`test`; override with `js-command` etc., and hide directories from detection with `exclude-paths`.
  - `build.yml` (pnpm + turbo repos): one `turbo run build`, uploading the turbo cache as an artifact for later stages.
  - `e2e.yml`: restores that cache, starts only the services in the repo's own compose file (none if absent), runs `test:e2e`.
  - `security.yml`: `pnpm audit` or `npm audit` by the root lockfile, `cargo audit` (Rust repos), gitleaks.
  - `pr-housekeeping.yml`: requires a `.changeset/*.md` when `packages/` changed (repos with changesets), rejects WIP titles.
- `pick-runner.yml`: decides hosted vs self-hosted for the caller's jobs (`.github/scripts/pick_runner.py`) and writes the choice and its reason to the run summary. First match wins: fork PR → hosted, always; `runner` input (`auto|hosted|self-hosted`, default `auto`); a `runner: hosted|self-hosted [workflow,…]` line in the PR description, e.g. `runner: self-hosted` (all workflows) or `runner: hosted tests,e2e` (only those workflow keys); the last line naming the workflow wins, an unknown kind is ignored with a warning, and fork PRs ignore it. It is read via the API at run time, so an edit applies on the next push or re-run without cancelling anything (there is deliberately no `edited`/`labeled` trigger: with cancel-in-progress concurrency it would cancel in-flight runs, and a label trigger re-runs everything for unrelated labels); the caller's entry in repo var `GH_RUNNER_MAP` (JSON keyed by workflow file name without `.yml`, e.g. `{"tests":"self-hosted","e2e":"hosted"}`); else hosted (`ubuntu-latest`). Invalid map JSON or an unknown value means hosted plus a warning. Self-hosted labels come from `vars.GH_RUNNER` (JSON list, default `["self-hosted","linux","x64","ubuntu-24.04"]`), with per-job size labels from `sizes` appended on self-hosted only. Callers expose `runner` as a choice input on `workflow_dispatch`/`workflow_call` and pass it through:

  ```yaml
  jobs:
    runner:
      uses: prosopo/github_actions/.github/workflows/pick-runner.yml@<sha>
      permissions: { contents: read, pull-requests: read }
      with:
        actions-ref: <sha>
        runner: ${{ inputs.runner || 'auto' }}
        sizes: '{"build":["xlarge"]}'
    build:
      needs: runner
      runs-on: ${{ fromJSON(needs.runner.outputs.labels).build }}
    lint:
      needs: runner
      runs-on: ${{ fromJSON(needs.runner.outputs.default) }}
  ```

  Two calls in one run need distinct `workflow` or `concurrency-key` inputs, or they queue behind each other. `ci.yml`'s `selftest-pick-runner` jobs call it as above and check the output shape.
- `ci-coverage-audit.yml`: weekly (and on dispatch), the CI coverage matrix for every repo in `.github/scripts/ciaudit/repos.txt`: per unit (package, crate, Python project, Dockerfile, ...) whether CI builds, lints, typechecks, tests and e2e-tests it, from a static walk of each triggered workflow (reusable workflows, composite actions, package scripts, turbo tasks) cross-checked against its latest green run. HTML and JSON in the `ci-coverage` artifact, a text matrix in the step summary. A gap that is deliberate goes in `.github/scripts/ciaudit/optouts.json` with its reason. Run locally: `fetch.py --repos-file repos.txt --dest-prefix snap/` then `audit.py --snap-prefix snap/ --repos-file repos.txt --out-json r.json --out-html r.html`; `why.py` prints the evidence behind one unit.
- `auto-update-pr.yml`: updates one random behind, mergeable, auto-merge PR at a time (`.github/scripts/auto_update_pr.py`).

## This repo's own CI

- `ci.yml` (`check`, required): calls this repo's own `static.yml` and `test.yml` from the PR's checkout, so a PR exercises its edits to them.
  - `static`: the js job runs `pnpm run setup:lint-tools` then `pnpm run lint` (package.json: actionlint, yamllint, taplo, shellcheck of composite action shell via `.github/scripts/lint_composite_shell.py`, `.github/scripts/validate_actions.py` for every action.yml's metadata, which actionlint does not read, and check-timeouts); the python job runs `pnpm run setup:py-tools` (pinned ruff) then `pnpm run lint:py`, which byte-compiles every script under `.github` and runs `ruff check` over them with the rules in `ruff.toml`.
  - `test`: the python job runs `pnpm run test:py` (`.github/scripts/run_python_tests.py`: unittest in every directory holding `test_*.py`, found by glob).
  - `selftest-static` / `selftest-test`: the same stages against one fixture per language under `.github/fixtures` (js-only, npm-only, python-only, go-only, php-only): each fixture's own job must pass and every other language's job must be skipped.
  - `check` fails unless every stage succeeded or was skipped, and on drafts.
  - Run any piece locally with the same script, e.g. `pnpm run lint:yaml` or `pnpm run test:py` (Node from `.nvmrc`).
- `actions-selftest.yml` (`actions-test`, required): runs restore-cache/save-cache (a miss, a save, then an exact hit in a second job) and setup-pnpm (against `tests/fixtures/pnpm`) on ubuntu and macOS, as consumers call them. `actions-test` fails unless every test job succeeded, and on drafts.

## Cache rules (restore-cache / save-cache)

- Dependency caches are keyed on lockfiles; build outputs on hashFiles of their inputs, with prefix fallback.
- Main saves; PRs never save dependency stores, and keep one build entry per name (older ones deleted).
- No save after an exact hit or when the restored tree is unchanged (`fingerprint`).
