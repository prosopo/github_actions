# print_contexts

Utility action for debugging that prints various GitHub Actions contexts as JSON output in collapsible groups.

## What it does

Outputs the following GitHub Actions contexts for debugging workflows:

- `github` - GitHub context (event, repository, workflow, actor, etc.)
- `env` - Environment variable names (values are not printed)
- `vars` - Repository/organization variables
- `job` - Current job context
- `runner` - Runner information
- `secrets` - Secret names (if provided); every value is registered with `::add-mask::` and never printed
- `strategy` - Job matrix strategy information
- `matrix` - Current matrix values
- `needs` - Results from dependent jobs
- `inputs` - Workflow/action inputs
- `steps` - Step outputs

Each context is printed in a collapsible group for easy reading in GitHub Actions logs. `github.token` is dropped.

Then each selected probe prints in its own group:

| Probe | Prints |
|-------|--------|
| `tools` (default) | node, pnpm and docker versions |
| `npm` | `npm whoami` and the configured registry |
| `docker` | registries in the docker config's `auths` (names only, not credentials) |
| `aws` | `aws sts get-caller-identity` |
| `gh` | the login of the token `gh` uses (set `GH_TOKEN` on the step) |

A probe that is not installed, fails or takes over 20s prints why and the step carries on: diagnostics never fail the job.

Contexts are passed to `print_contexts.py` through env vars, not inlined into the shell script, so a quote in a value cannot break or inject into it. Tests: `python3 -m unittest test_print_contexts` in this directory.

## Usage

```yaml
- uses: prosopo/github_actions/.github/actions/print_contexts@gha
  with:
    INPUTS_CONTEXT: ${{ toJson(inputs) }}
    NEEDS_CONTEXT: ${{ toJson(needs) }}
    VARS_CONTEXT: ${{ toJson(vars) }}
    SECRETS_CONTEXT: ${{ toJson(secrets) }}
    STEPS_CONTEXT: ${{ toJson(steps) }}
    probes: tools,npm,docker
```

## Inputs

All inputs are optional; the `*_CONTEXT` ones are JSON strings:

| Input | Type | Required | Description |
|-------|------|----------|-------------|
| `INPUTS_CONTEXT` | string | false | The inputs context as JSON |
| `NEEDS_CONTEXT` | string | false | The needs context as JSON |
| `VARS_CONTEXT` | string | false | The vars context as JSON |
| `SECRETS_CONTEXT` | string | false | The secrets context as JSON |
| `STEPS_CONTEXT` | string | false | The steps context as JSON |
| `probes` | string | false | Comma-separated probes (default `tools`) |

## Outputs

This action produces no outputs.

## Use cases

- Debugging workflow execution and understanding what values are available at runtime
- Understanding job dependencies and their outputs
- Inspecting environment variables and GitHub-provided context
- Troubleshooting matrix strategy execution
- Verifying secrets and variables are properly configured

## Notes

- Passing `SECRETS_CONTEXT` prints names only; values are masked, so it is safe in publish and deploy jobs
- Most contexts (`github`, `env`, `job`, `runner`, `strategy`, `matrix`) are always printed
- This action is primarily useful for development and debugging workflows
