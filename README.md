# Synaptic PR Review

AI-powered PR review with anti-hallucination rules, severity callouts, and Mermaid diagrams.

## Quick Start

**1.** Create a secret at **Settings → Secrets and variables → Actions → New repository secret** with your API key.

**2.** Add to your workflow:

### OpenCode (free tier available)

```yaml
name: AI PR Review
on:
  pull_request:
    types: [opened, synchronize]
permissions:
  pull-requests: write
  contents: read
jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: minhphu102003/ai-pr-review-action@v0.0.9
        with:
          opencode_api_key: ${{ secrets.OPENCODE_API_KEY }}
```

> **⚠️ Warning:** OpenCode may commit and push LLM-generated code to your branch (`contents: write`). Use on non-default branches or draft PRs.

### Direct OpenAI

```yaml
      - uses: minhphu102003/ai-pr-review-action@v0.0.9
        with:
          engine: direct
          model: gpt-4.1-mini
          openai_api_key: ${{ secrets.OPENAI_API_KEY }}
```

### Direct Anthropic

```yaml
      - uses: minhphu102003/ai-pr-review-action@v0.0.9
        with:
          engine: direct
          model: claude-haiku-4-5-20251001
          anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
```

## Security

> **Always store API keys as [GitHub Actions secrets](https://docs.github.com/en/actions/security-for-github-actions/security-guides/using-secrets-in-github-actions). Never hardcode keys in your workflow file.**

The action masks all API key values in workflow logs automatically.

| Permission | Why |
|------------|-----|
| `pull-requests: write` | Post review comments |
| `contents: read` | Read PR files |
| `contents: write` | *(OpenCode only)* Auto-commit changes |

## Inputs

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `engine` | No | `opencode` | `opencode` or `direct` |
| `model` | No | *(auto)* | Model name |
| `opencode_api_key` | If opencode | - | OpenCode API key (secret) |
| `openai_api_key` | If direct | - | OpenAI API key (secret) |
| `openai_base_url` | No | - | Custom OpenAI-compatible URL |
| `anthropic_api_key` | If direct | - | Anthropic API key (secret) |
| `anthropic_base_url` | No | - | Custom Anthropic-compatible URL |
| `github_token` | Yes | `${{ github.token }}` | GitHub token |
| `prompt_file` | No | *(built-in)* | Custom prompt file path |
| `exclude` | No | - | Glob patterns to exclude (direct only) |
| `update_comment` | No | `true` | Update existing comment (direct only) |
| `share` | No | `false` | Share OpenCode session link (opencode only) |

**Defaults:** `opencode` → `opencode/mimo-v2.5-free`, `direct` (OpenAI) → `gpt-4.1-mini`, `direct` (Anthropic) → `claude-haiku-4-5-20251001`

## Compatible APIs

Use `openai_base_url` or `anthropic_base_url` for any compatible provider:

| Provider | `openai_base_url` | Example model |
|----------|-------------------|---------------|
| OpenRouter | `https://openrouter.ai/api/v1` | `anthropic/claude-3.5-haiku` |
| Groq | `https://api.groq.com/openai/v1` | `llama-3.1-70b-versatile` |
| Together AI | `https://api.together.xyz/v1` | `meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo` |
| Ollama | `http://localhost:11434/v1` | `llama3.1` |

## On-demand Review

Comment `/oc` or `/review` on a PR to trigger re-review. Requires `issues: write` permission. See [workflow example](https://github.com/minhphu102003/ai-pr-review-action/blob/main/action.yml).

## Advanced

**Custom prompt:** Place at `.github/prompts/review.txt` (auto-detected) or set `prompt_file` input.

**Fork PRs:** Default `github.token` is read-only on forks. Use `pull_request_target` — see [security implications](https://securitylab.github.com/research/github-actions-preventing-pwn-requests/).

**Cost control:** Use free-tier models (`opencode/mimo-v2.5-free`), lightweight models (`gpt-4.1-mini`), or add `paths-ignore` to skip docs/lock files.

## License

[MIT](LICENSE) — This action is provided "as is" without warranty of any kind. Use at your own risk.

## Contributing

- [Contributing Guide](CONTRIBUTING.md) | [Code of Conduct](CODE_OF_CONDUCT.md) | [Security Policy](SECURITY.md) | [Changelog](CHANGELOG.md)
