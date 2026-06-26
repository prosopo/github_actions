# save_turbo_cache

Saves the turbo build-output cache (`.turbo`) to GitHub Actions cache storage.

This is kept separate from the npm cache (see `save_npm_cache`) because turbo build outputs invalidate on (almost) every commit, whereas the npm cache only changes when the lockfile does. Storing them separately means the stable npm cache is not re-uploaded every time the build changes.

## What it does

- Creates the cache directory (`.turbo`)
- Saves the turbo build outputs using `actions/cache/save@v4`
- Automatically cleans up old caches, keeping only the latest one to manage storage

## Usage

```yaml
- uses: prosopo/github_actions/.github/actions/save_turbo_cache@main
```

## Inputs

This action accepts no inputs.

## Outputs

This action produces no outputs.

## Cache paths

- `.turbo` - turbo build cache

## Cache cleanup

The action automatically removes all old caches with matching keys, keeping only the most recently created cache. This prevents cache storage from growing unbounded while ensuring subsequent runs can benefit from previously cached artifacts.

Cache keys are based on:
- `runner.os` - Operating system
- `runner.arch` - CPU architecture
- `github.run_id` - Unique workflow run ID
- `github.run_attempt` - Attempt number within the run

## Notes

- This action should be used after the build steps in a workflow to capture turbo build outputs for future runs
