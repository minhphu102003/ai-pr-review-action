# AI PR Review Action

AI-powered GitHub PR review with anti-hallucination rules, GitHub callout severity colors, and Mermaid flow diagrams.

## Features

- **Anti-hallucination rules** — prevents AI from inventing function signatures or flagging non-existent issues
- **Severity callouts** — uses GitHub's native colored callouts: `[!CAUTION]` (red), `[!WARNING]` (yellow), `[!NOTE]` (blue)
- **Mermaid diagrams** — auto-generates flow diagrams for every PR (sequenceDiagram for endpoints, flowchart for logic)
- **Dual engine** — use OpenCode (free tier available) or direct API (OpenAI, Anthropic)
- **Customizable prompt** — override the built-in prompt with your own review guidelines

## Quick Start

### Minimal setup (OpenCode engine)

```yaml
# .github/workflows/review.yml
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
      - uses: minhphu102003/ai-pr-review-action@v0.0.5
        with:
          opencode_api_key: ${{ secrets.OPENCODE_API_KEY }}
```

### Direct Anthropic API (Haiku — fast, cheap, 200k context)

```yaml
      - uses: minhphu102003/ai-pr-review-action@v0.0.5
        with:
          engine: direct
          model: claude-haiku-4-5-20251001
          anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
```

### Direct OpenAI API (GPT-4.1 Mini — fast, cheap, 128k context)

```yaml
      - uses: minhphu102003/ai-pr-review-action@v0.0.5
        with:
          engine: direct
          model: gpt-4.1-mini
          openai_api_key: ${{ secrets.OPENAI_API_KEY }}
```

## Inputs

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `engine` | No | `opencode` | LLM engine: `opencode` or `direct` |
| `model` | No | *(auto)* | Model name (e.g. `gpt-4.1-mini`, `claude-haiku-4-5-20251001`) |
| `opencode_api_key` | If engine=opencode | - | OpenCode API key |
| `openai_api_key` | If engine=direct | - | OpenAI API key |
| `openai_base_url` | No | `https://api.openai.com/v1` | Custom base URL for OpenAI-compatible API |
| `anthropic_api_key` | If engine=direct | - | Anthropic API key |
| `anthropic_base_url` | No | `https://api.anthropic.com` | Custom base URL for Anthropic-compatible API |
| `github_token` | Yes | `${{ github.token }}` | GitHub token for posting comments |
| `prompt_file` | No | *(built-in)* | Path to custom prompt file in your repo |
| `exclude` | No | - | Comma-separated glob patterns to exclude (e.g. `docs/**,*.md`) |
| `update_comment` | No | `true` | Update existing review comment instead of creating a new one |

### Default models per engine

| Engine | Default model |
|--------|--------------|
| `opencode` | `opencode/mimo-v2.5-free` |
| `direct` (OpenAI) | `gpt-4.1-mini` |
| `direct` (Anthropic) | `claude-haiku-4-5-20251001` |

## Compatible APIs

Use `openai_base_url` or `anthropic_base_url` to connect to any OpenAI/Anthropic-compatible provider:

**OpenRouter:**
```yaml
      - uses: minhphu102003/ai-pr-review-action@v0.0.5
        with:
          engine: direct
          model: anthropic/claude-3.5-haiku
          openai_api_key: ${{ secrets.OPENROUTER_API_KEY }}
          openai_base_url: https://openrouter.ai/api/v1
```

**Groq:**
```yaml
      - uses: minhphu102003/ai-pr-review-action@v0.0.5
        with:
          engine: direct
          model: llama-3.1-70b-versatile
          openai_api_key: ${{ secrets.GROQ_API_KEY }}
          openai_base_url: https://api.groq.com/openai/v1
```

**Together AI:**
```yaml
      - uses: minhphu102003/ai-pr-review-action@v0.0.5
        with:
          engine: direct
          model: meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo
          openai_api_key: ${{ secrets.TOGETHER_API_KEY }}
          openai_base_url: https://api.together.xyz/v1
```

**Ollama (local):**
```yaml
      - uses: minhphu102003/ai-pr-review-action@v0.0.5
        with:
          engine: direct
          model: llama3.1
          openai_api_key: ollama
          openai_base_url: http://localhost:11434/v1
```

## Custom Prompt

The action includes a built-in prompt optimized for thorough code review. You can override it:

**Option 1:** Place your prompt at `.github/prompts/review.txt` in your repo (auto-detected).

**Option 2:** Specify a custom path:
```yaml
      - uses: minhphu102003/ai-pr-review-action@v0.0.5
        with:
          prompt_file: my-custom-review-prompt.txt
          opencode_api_key: ${{ secrets.OPENCODE_API_KEY }}
```

### Prompt resolution order

1. `prompt_file` input (if set and file exists)
2. `.github/prompts/review.txt` in caller's repo (if exists)
3. Built-in prompt (`prompts/review.txt` in this action)

## On-demand Review

Trigger a re-review by commenting `/oc` or `/review` on a PR:

```yaml
# .github/workflows/review.yml
name: AI PR Review
on:
  pull_request:
    types: [opened, synchronize]
  issue_comment:
    types: [created]
  pull_request_review_comment:
    types: [created]

permissions:
  contents: read
  pull-requests: write
  issues: write

jobs:
  auto-review:
    if: github.event_name == 'pull_request' && !github.event.pull_request.draft
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: minhphu102003/ai-pr-review-action@v0.0.5
        with:
          opencode_api_key: ${{ secrets.OPENCODE_API_KEY }}

  comment-review:
    if: >-
      github.event_name != 'pull_request' &&
      github.event.comment.user.type != 'Bot' &&
      (github.event.comment.body == '/oc' ||
       github.event.comment.body == '/review' ||
       startsWith(github.event.comment.body, '/oc ') ||
       startsWith(github.event.comment.body, '/review ')) &&
      (github.event.issue.pull_request ||
       github.event_name == 'pull_request_review_comment')
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: minhphu102003/ai-pr-review-action@v0.0.5
        with:
          opencode_api_key: ${{ secrets.OPENCODE_API_KEY }}
```

## Review Output

The review comment uses GitHub callout syntax for visual severity:

- `[!CAUTION]` (red border) — Critical bugs, security issues
- `[!WARNING]` (yellow border) — Warnings, potential issues
- `[!NOTE]` (blue border) — Suggestions, improvements

Each issue includes the problem code and a suggested fix, plus a Mermaid diagram showing the affected flow.

## Cost Considerations

The action calls a paid LLM API on every trigger. To control costs:

- Use free-tier models: `opencode/mimo-v2.5-free` (OpenCode engine)
- Use lightweight models: `gpt-4.1-mini`, `claude-haiku-4-5-20251001`
- Filter paths to avoid reviewing docs/lock files: add `paths-ignore: ['**/*.md', '**/package-lock.json']` to your workflow
- The `synchronize` event triggers on every push — consider removing it if you only want initial reviews

## Fork PRs

When a PR is opened from a fork, the default `github.token` has read-only permissions and cannot post comments. Use `pull_request_target` instead of `pull_request` for fork PRs, but be aware of the [security implications](https://securitylab.github.com/research/github-actions-preventing-pwn-requests/).

## Requirements

- GitHub Actions runner (ubuntu-latest)
- Python 3.10+ (for direct engine only — pre-installed on GitHub-hosted runners)
- API key for your chosen engine

## License

MIT
