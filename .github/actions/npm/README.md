# npm

Sets up Node.js environment and manages npm dependencies.

## What it does

- Configures Node.js version from `package.json` using `actions/setup-node@v4`
- Optionally restores npm cache using `restore_npm_cache` action
- Installs a specific npm version globally (currently npm@11.6.2)
- Runs `npm ci` to install project dependencies (if enabled)

## Usage

```yaml
- uses: prosopo/github_actions/.github/actions/npm@gha
  with:
    npm_ci: true
    npm_ci_args: '--legacy-peer-deps'
    restore_npm_cache: true
```

## Inputs

| Input | Type | Default | Required | Description |
|-------|------|---------|----------|-------------|
| `npm_ci` | boolean | `true` | false | Run `npm ci` to install dependencies |
| `npm_ci_args` | string | `''` | false | Additional arguments to pass to `npm ci` |
| `restore_npm_cache` | boolean | `true` | false | Restore npm cache before installation |

## Outputs

This action produces no outputs.

## Notes

- Node.js version is automatically determined from the `engines.npm` field in `package.json`
- The npm cache restore is a separate concern and can be disabled for workflows that handle caching differently
