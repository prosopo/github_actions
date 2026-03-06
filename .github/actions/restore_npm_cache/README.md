# restore_npm_cache

Restores npm, Cypress, Turbo, and Nx caches from GitHub Actions cache storage.

## What it does

- Creates required cache directories (`~/.npm`, `~/.cache/Cypress`, `.turbo`, `.nx`)
- Restores cached npm packages, Cypress binaries, and build tool caches using `actions/cache/restore@v4`
- Uses wildcard cache key matching to find the most recent compatible cache
- Skips restoration on self-hosted runners by default (network performance optimization) - can be enabled with `restore-on-self-hosted` input

## Usage

```yaml
- uses: prosopo/captcha/.github/actions/restore_npm_cache@gha
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
- `.turbo` - Turbo build cache
- `.nx` - Nx build cache

## Notes

- This action is automatically called by the `npm` action if `restore_npm_cache` input is set to `true`
- Cache restoration is skipped on self-hosted runners by default due to network performance considerations. Use `restore-on-self-hosted: 'true'` to enable it
- The wildcard cache key (`npm-${{ runner.os }}-${{ runner.arch }}-`) matches any cache from the same OS and architecture
