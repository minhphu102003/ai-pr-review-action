# AI PR Review Action

GitHub Action for AI-powered PR review with anti-hallucination rules, severity callouts, and Mermaid diagrams.

---

## Before changing tags or versions

**MUST** read `.agents/skills/version-convention.md` before creating, updating, or deleting any git tag. This file defines semver rules and guardrails that apply to all version operations.

---

## Project Structure

```
action.yml              # Composite action definition
scripts/
  review_context.py     # Shared utilities (GitHub API, review processing, context formatting)
  post_inline.py        # OpenCode engine post-processing (inline comments, replies)
  review_direct.py      # Direct LLM API engine (OpenAI, Anthropic)
prompts/
  review.txt            # Built-in review prompt
.agents/
  skills/
    version-convention.md  # Semantic versioning rules
```

## Conventions

- **Python**: stdlib only (no pip dependencies), Python 3.10+
- **Shell**: bash for composite action steps, never use `${{ inputs.* }}` in `run:` blocks (use env vars)
- **Tags**: `vMAJOR.MINOR.PATCH` — never force-update, always bump
- **Branches**: `main` (default), `feat/`, `fix/`, `chore/` prefixes
- **Commits**: `<type>: <description>` — e.g., `fix: handle edge cases in filter_diff`

## Quick checks

```bash
python -c "import py_compile; [py_compile.compile(f'scripts/{f}', doraise=True) for f in ('review_context.py','post_inline.py','review_direct.py')]"  # Valid Python
python -c "import yaml; yaml.safe_load(open('action.yml', encoding='utf-8'))"                                                                       # Valid YAML
```
