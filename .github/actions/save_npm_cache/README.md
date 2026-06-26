# save_npm_cache

Saves the npm-ecosystem install cache (npm package cache + Cypress binary) to GitHub Actions cache storage.

Build outputs (turbo) are saved separately — see `save_turbo_cache`. They are kept apart because turbo invalidates on (almost) every commit, whereas this cache only changes when the lockfile does; bundling them would force the stable npm cache to be re-uploaded on every run.

## What it does

- Creates cache directories (`~/.npm`, `~/.cache/Cypress`)
- Verifies npm cache integrity with `npm cache verify`
- Saves cached npm packages and the Cypress binary using `actions/cache/save@v4`
- Automatically cleans up old caches, keeping only the latest one to manage storage

## Usage

```yaml
- uses: prosopo/github_actions/.github/actions/save_npm_cache@main
```

## Inputs

This action accepts no inputs.

## Outputs

This action produces no outputs.

## Cache paths

- `~/.npm` - npm package cache
- `~/.cache/Cypress` - Cypress binary cache

## Cache cleanup

The action automatically removes all old caches with matching keys, keeping only the most recently created cache. This prevents cache storage from growing unbounded while ensuring subsequent runs can benefit from previously cached artifacts.

Cache keys are based on:
- `runner.os` - Operating system
- `runner.arch` - CPU architecture
- `github.run_id` - Unique workflow run ID
- `github.run_attempt` - Attempt number within the run

## Notes

- Cache verification ensures the saved cache is valid before cleanup
- This action should be used in the final step of a workflow to capture install artifacts for future runs
- Self-hosted runners can also use this action (unlike restore, which skips them)
