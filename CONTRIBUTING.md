# Contributing to AI PR Review Action

Thanks for your interest in contributing!

## What does this repo do?

This is a **composite GitHub Action** that reviews PRs using LLM APIs. It has two engines:

- **OpenCode** — uses the [OpenCode](https://github.com/anomalyco/opencode) service (default)
- **Direct** — calls OpenAI or Anthropic APIs directly via a Python script (`scripts/review_direct.py`)

The action is designed to work with **zero dependencies** (Python stdlib only) and supports any OpenAI/Anthropic-compatible provider through custom base URLs.

## Submitting a Pull Request

1. Fork and clone the repository
2. Create a branch from `main`: `git checkout -b feat/your-feature`
3. Make your changes
4. Validate Python syntax:
   ```bash
   python -c "import py_compile; py_compile.compile('scripts/review_direct.py', doraise=True)"
   ```
5. Validate YAML:
   ```bash
   python -c "import yaml; yaml.safe_load(open('action.yml', encoding='utf-8'))"
   ```
6. Test the action in a real workflow (create a test repo, open a test PR)
7. Push and submit your PR

## Guidelines

- Keep changes focused — one concern per PR
- Write clear commit messages: `feat:`, `fix:`, `docs:`, `chore:`
- Follow [Semantic Versioning](https://semver.org/) — see `.agents/skills/version-convention.md`
- Never use `${{ inputs.* }}` in `run:` blocks — use `env:` instead (security)
- Python code must use stdlib only (no pip dependencies)

## Reporting Bugs

Open an issue with:
- Clear title and description
- Steps to reproduce
- Expected vs actual behavior
- Action version, engine (opencode/direct), and model used

## Resources

- [GitHub Actions documentation](https://docs.github.com/en/actions)
- [Composite actions](https://docs.github.com/en/actions/creating-actions/creating-a-composite-action)
- [Contributor Covenant](https://www.contributor-covenant.org/)
