# restore_npm_cache

Restores the npm-ecosystem install cache (npm package cache + Cypress binary) from GitHub Actions cache storage.

Build outputs (turbo) are cached separately — see `restore_turbo_cache`. They are kept apart because turbo invalidates on (almost) every commit, whereas this cache only changes when the lockfile does; bundling them would force the stable npm cache to be re-uploaded on every run.

## What it does

- Creates required cache directories (`~/.npm`, `~/.cache/Cypress`)
- Restores cached npm packages and the Cypress binary using `actions/cache/restore@v4`
- Uses wildcard cache key matching to find the most recent compatible cache
- Skips restoration on self-hosted runners by default (network performance optimization) - can be enabled with `restore-on-self-hosted` input

## Usage

```yaml
- uses: prosopo/github_actions/.github/actions/restore_npm_cache@main
```

## Inputs

| Input | Description | Required | Default |
|-------|-------------|----------|---------|
| `restore-on-self-hosted` | Whether to restore npm cache on self-hosted runners | false | `false` |

## Outputs

This action produces no outputs.

## Cache paths

- `~/.npm` - npm package cache
- `~/.cache/Cypress` - Cypress binary cache

## Notes

- This action is automatically called by the `npm` action if `restore_npm_cache` input is set to `true`
- Cache restoration is skipped on self-hosted runners by default due to network performance considerations. Use `restore-on-self-hosted: 'true'` to enable it
- The wildcard cache key (`npm-${{ runner.os }}-${{ runner.arch }}-`) matches any cache from the same OS and architecture
