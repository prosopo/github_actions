# save_cargo_cache

Saves Rust/Cargo registry, git cache, and build artifacts to GitHub Actions cache storage.

## What it does

- Creates required Cargo directories (`~/.cargo`, `~/.cargo/registry`, `~/.cargo/git`, `target`)
- Saves Cargo registry, git dependencies, and build artifacts using `actions/cache/save@v4`
- Automatically cleans up old caches, keeping only the latest one to manage storage

## Usage

```yaml
- uses: prosopo/github_actions/.github/actions/save_cargo_cache@gha
```

## Inputs

This action accepts no inputs.

## Outputs

This action produces no outputs.

## Cache paths

- `~/.cargo/registry` - Cargo registry cache
- `~/.cargo/git` - Cargo git dependencies cache
- `target` - Build artifacts (root level)
- `**/target` - Build artifacts (all subdirectories)
- `!**/target/**/target` - Excludes nested target directories

## Cache cleanup

The action automatically removes all old caches with matching keys, keeping only the most recently created cache. This prevents cache storage from growing unbounded while ensuring subsequent runs can benefit from previously cached artifacts.

Cache keys are based on:
- `runner.os` - Operating system
- `runner.arch` - CPU architecture
- `github.run_id` - Unique workflow run ID
- `github.run_attempt` - Attempt number within the run

## Notes

- This action should be used in the final step of a workflow to capture build artifacts for future runs
- This action is commonly used in Rust/Cargo workflows to speed up subsequent builds
- Cache cleanup uses the GitHub CLI (`gh`) to manage cache keys
