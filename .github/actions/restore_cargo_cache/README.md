# restore_cargo_cache

Restores Rust/Cargo registry, git cache, and build artifacts from GitHub Actions cache storage.

## What it does

- Creates required Cargo directories (`~/.cargo`, `~/.cargo/registry`, `~/.cargo/git`, `target`)
- Restores cached Cargo registry, git dependencies, and build artifacts using `actions/cache/restore@v4`
- Uses wildcard cache key matching to find the most recent compatible cache
- Skips restoration on self-hosted runners by default (network performance optimization)
- Allows cache restoration on self-hosted runners via the `restore-on-self-hosted` input

## Usage

```yaml
- uses: prosopo/captcha/.github/actions/restore_cargo_cache@gha
```

## Inputs

| Input | Description | Default |
|-------|-------------|---------|
| `restore-on-self-hosted` | Whether to restore cache on self-hosted runners | `false` |

## Outputs

This action produces no outputs.

## Cache paths

- `~/.cargo/registry` - Cargo registry cache
- `~/.cargo/git` - Cargo git dependencies cache
- `target` - Build artifacts (root level)
- `**/target` - Build artifacts (all subdirectories)
- `!**/target/**/target` - Excludes nested target directories

## Notes

- This action is commonly used in Rust/Cargo workflows to speed up builds
- Cache restoration is skipped on self-hosted runners by default due to network performance considerations
- Set `restore-on-self-hosted: 'true'` to enable cache restoration on self-hosted runners
- The wildcard cache key (`cargo-${{ runner.os }}-${{ runner.arch }}-`) matches any cache from the same OS and architecture
- Directories must exist before cache restore can occur (this action creates them)

## Example with self-hosted cache restoration

```yaml
- uses: prosopo/captcha/.github/actions/restore_cargo_cache@gha
  with:
    restore-on-self-hosted: 'true'
```
