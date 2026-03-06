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
