# restore_turbo_cache

Restores the turbo build-output cache (`.turbo`) from GitHub Actions cache storage.

This is kept separate from the npm cache (see `restore_npm_cache`) because turbo build outputs invalidate on (almost) every commit, whereas the npm cache only changes when the lockfile does. Storing them separately means the stable npm cache is not re-uploaded every time the build changes.

## What it does

- Creates the cache directory (`.turbo`)
- Restores the cached turbo build outputs using `actions/cache/restore@v4`
- Uses wildcard cache key matching to find the most recent compatible cache
- Skips restoration on self-hosted runners by default (network performance optimization) - can be enabled with `restore-on-self-hosted` input

## Usage

```yaml
- uses: prosopo/github_actions/.github/actions/restore_turbo_cache@main
```

## Inputs

| Input | Description | Required | Default |
|-------|-------------|----------|---------|
| `restore-on-self-hosted` | Whether to restore the turbo cache on self-hosted runners | false | `false` |

## Outputs

This action produces no outputs.

## Cache paths

- `.turbo` - turbo build cache

## Notes

- Cache restoration is skipped on self-hosted runners by default due to network performance considerations. Use `restore-on-self-hosted: 'true'` to enable it
- The wildcard cache key (`turbo-${{ runner.os }}-${{ runner.arch }}-`) matches any cache from the same OS and architecture
