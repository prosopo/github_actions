# print_contexts

Utility action for debugging that prints various GitHub Actions contexts as JSON output in collapsible groups.

## What it does

Outputs the following GitHub Actions contexts for debugging workflows:

- `github` - GitHub context (event, repository, workflow, actor, etc.)
- `env` - Environment variables
- `vars` - Repository/organization variables
- `job` - Current job context
- `runner` - Runner information
- `secrets` - Repository/organization secrets (if provided)
- `strategy` - Job matrix strategy information
- `matrix` - Current matrix values
- `needs` - Results from dependent jobs
- `inputs` - Workflow/action inputs
- `steps` - Step outputs

Each context is printed in a collapsible group for easy reading in GitHub Actions logs.

## Usage

```yaml
- uses: prosopo/captcha/.github/actions/print_contexts@gha
  with:
    INPUTS_CONTEXT: ${{ toJson(inputs) }}
    NEEDS_CONTEXT: ${{ toJson(needs) }}
    VARS_CONTEXT: ${{ toJson(vars) }}
    SECRETS_CONTEXT: ${{ toJson(secrets) }}
    STEPS_CONTEXT: ${{ toJson(steps) }}
```

## Inputs

All inputs are optional JSON strings:

| Input | Type | Required | Description |
|-------|------|----------|-------------|
| `INPUTS_CONTEXT` | string | false | The inputs context as JSON |
| `NEEDS_CONTEXT` | string | false | The needs context as JSON |
| `VARS_CONTEXT` | string | false | The vars context as JSON |
| `SECRETS_CONTEXT` | string | false | The secrets context as JSON |
| `STEPS_CONTEXT` | string | false | The steps context as JSON |

## Outputs

This action produces no outputs.

## Use cases

- Debugging workflow execution and understanding what values are available at runtime
- Understanding job dependencies and their outputs
- Inspecting environment variables and GitHub-provided context
- Troubleshooting matrix strategy execution
- Verifying secrets and variables are properly configured

## Notes

- The `secrets` context is sensitive - only include if necessary for debugging
- Most contexts (`github`, `env`, `job`, `runner`, `strategy`, `matrix`) are always printed
- This action is primarily useful for development and debugging workflows
