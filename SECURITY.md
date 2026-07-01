# Security Policy

## Supported Versions

| Version | Supported          |
|---------|--------------------|
| 0.0.x   | :white_check_mark: |

## Reporting a Vulnerability

If you discover a security vulnerability, please report it responsibly.

**Do NOT open a public issue for security vulnerabilities.**

### How to Report

1. **GitHub DM**: Send a direct message to [@minhphu102003](https://github.com/minhphu102003)
2. **Email**: Contact via GitHub profile email

### What to Include

- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

### Response Timeline

- **Acknowledgment**: Within 48 hours
- **Initial assessment**: Within 1 week
- **Fix release**: Depends on severity (critical issues within days, others in next release)

## Security Considerations

This action:

- Passes all API keys through environment variables (never inline in shell commands)
- Masks API key values in workflow logs via `::add-mask::`
- Validates API responses before processing
- Does not store or log API keys
- Uses HTTPS for all API calls

### Required Permissions

| Permission | Engine | Why |
|------------|--------|-----|
| `contents: read` | Both | Read PR files for review |
| `pull-requests: write` | Both | Post review comments |
| `issues: write` | Both | Manage review reactions + on-demand review |
| `contents: write` | OpenCode | Auto-commit LLM-generated changes |

### Known Risks

- **LLM output**: The action posts raw LLM output as PR comments. While `@mentions` are sanitized, prompt injection through diff content is theoretically possible.
- **Auto-commit (OpenCode engine)**: The OpenCode engine may commit and push LLM-generated code to your branch. Use on non-default branches.
- **API keys**: Users must use GitHub Secrets for all API keys. Never hardcode keys in workflow files.
- **Third-party dependency**: The OpenCode engine uses [`anomalyco/opencode/github@v1.17.12`](https://github.com/anomalyco/opencode).
