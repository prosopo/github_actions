# save_npm_cache

Saves npm, Cypress, Turbo, and Nx caches to GitHub Actions cache storage.

## What it does

- Creates cache directories with proper structure (`~/.npm`, `~/.cache/Cypress`, `.turbo/cache`, `.nx/cache`)
- Verifies npm cache integrity with `npm cache verify`
- Saves cached npm packages, Cypress binaries, and build tool caches using `actions/cache/save@v4`
- Automatically cleans up old caches, keeping only the latest one to manage storage

## Usage

```yaml
- uses: prosopo/github_actions/.github/actions/save_npm_cache@gha
```

## Inputs

This action accepts no inputs.

## Outputs

This action produces no outputs.

## Cache paths

- `~/.npm` - npm package cache
- `~/.cache/Cypress` - Cypress binary cache
- `.turbo/cache` - Turbo build cache
- `.nx/cache` - Nx build cache

## Cache cleanup

The action automatically removes all old caches with matching keys, keeping only the most recently created cache. This prevents cache storage from growing unbounded while ensuring subsequent runs can benefit from previously cached artifacts.

Cache keys are based on:
- `runner.os` - Operating system
- `runner.arch` - CPU architecture
- `github.run_id` - Unique workflow run ID
- `github.run_attempt` - Attempt number within the run

## Notes

- Cache verification ensures the saved cache is valid before cleanup
- This action should be used in the final step of a workflow to capture build artifacts for future runs
- Self-hosted runners can also use this action (unlike restore, which skips them)
