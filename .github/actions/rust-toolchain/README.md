# rust-toolchain

A GitHub Action that automatically installs the Rust toolchain based on your project's `rust-toolchain.toml` configuration.

## Usage

```yaml
- uses: ./.github/actions/rust-toolchain
```

## Description

This action reads the `rust-toolchain.toml` file from your project root and automatically extracts:

- **channel**: The Rust toolchain channel (e.g., `stable`, `nightly`, or a specific version)
- **targets**: Cross-compilation targets (e.g., `wasm32-unknown-unknown`)
- **components**: Additional components (e.g., `rustfmt`, `clippy`)

It then passes these values to the [dtolnay/rust-toolchain](https://github.com/dtolnay/rust-toolchain) action to install the configured toolchain.

## Configuration

Ensure your project has a `rust-toolchain.toml` file with the desired configuration:

```toml
[toolchain]
channel = "stable"
components = ["rustfmt", "clippy"]
targets = ["wasm32-unknown-unknown"]
```

All fields are optional and will be extracted as-is if present.

## Outputs

The action exposes the following outputs:

- `toolchain`: The extracted toolchain version
- `targets`: The comma-separated list of targets
- `components`: The comma-separated list of components

These can be used in subsequent steps if needed.
