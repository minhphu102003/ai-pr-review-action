---
name: version-convention
description: Follow Semantic Versioning (semver) for tags, releases, and dependency management. Enforces MAJOR.MINOR.PATCH bumping rules.
version: 1.0.0
---

# Version Convention Skill (Semantic Versioning)

## When to use

- Creating or updating git tags
- Publishing releases or packages
- Bumping dependency versions
- Deciding version number for new features, bug fixes, or breaking changes

## Goals

- **Predictable releases**: users know what to expect from a version bump
- **Safe upgrades**: patch versions are always safe to install
- **Clear communication**: version number tells the story of what changed
- **No force-updating tags**: each release gets its own immutable version

## Version format

```
MAJOR.MINOR.PATCH
  │      │     └── Bug fixes, patches, docs, minor improvements
  │      └──────── New features, backwards-compatible
  └─────────────── Breaking changes, API redesign
```

### Pre-release (0.x.y)

```
0.0.1  →  First pre-release
0.0.2  →  Bug fix on pre-release
0.0.3  →  Another bug fix
0.1.0  →  First feature added
0.1.1  →  Bug fix on that feature
0.2.0  →  Second feature added
1.0.0  →  First stable release
```

## Bump rules

| Change type | Example | Bump | From → To |
|---|---|---|---|
| Bug fix | Fix error handling in API call | PATCH | 0.1.0 → 0.1.1 |
| New feature | Add support for Anthropic API | MINOR | 0.1.1 → 0.2.0 |
| Breaking change | Rename input `api_key` → `llm_api_key` | MAJOR | 0.2.0 → 1.0.0 |
| Pre-release bug fix | Fix binary file filtering | PATCH | 0.0.1 → 0.0.2 |
| Pre-release feature | Add custom base URL support | MINOR | 0.0.2 → 0.1.0 |

## Git tag conventions

- **Tag format**: `vMAJOR.MINOR.PATCH` (e.g., `v0.0.1`, `v1.2.0`)
- **NEVER force-update a tag** — create a new version instead
- **NEVER reuse a version number** — even if the tag was wrong, bump to the next one
- **One commit = one version** — don't bundle unrelated changes into one tag

```bash
# Correct
git tag v0.0.2
git push origin v0.0.2

# Wrong — never do this
git tag -f v0.0.1
git push origin v0.0.1 --force
```

## Pre-release vs stable

| Phase | Version | Meaning |
|---|---|---|
| **Pre-release** | 0.x.y | API may change, not production-ready |
| **Stable** | 1.x.y | Production-ready, backwards-compatible guarantees |
| **Maintenance** | 2.x.y+ | Major version with breaking changes documented |

## GitHub Actions specific

- Pin action to major version for auto-updates: `@v1`
- Pin to exact version for reproducibility: `@v1.2.3`
- Pre-release: use `@v0.0.1` — community knows it's not stable yet
- Update tag reference in README examples when releasing new versions

## Execution steps

1. **Determine change type** — is it a fix, feature, or breaking change?
2. **Check current version** — `git tag --sort=-v:refname | head -5`
3. **Bump accordingly** — PATCH for fixes, MINOR for features, MAJOR for breaking
4. **Create tag** — `git tag v<new-version>`
5. **Push tag** — `git push origin v<new-version>`
6. **Update README** — if examples reference a specific version, update them

## Guardrails

- NEVER force-update an existing tag — always create a new version
- NEVER skip versions (don't go from 0.0.1 to 0.0.5)
- NEVER release without a tag — every public commit should have a version
- NEVER bundle breaking changes with bug fixes in the same release
- NEVER use `latest` in documentation examples — use explicit version
- Pre-release (0.x) means "expect breaking changes" — document this clearly
- When in doubt, bump PATCH — it's the safest choice

## Done criteria

- Version number accurately reflects the changes made
- Tag is created and pushed (not force-updated)
- README/examples reference the correct version
- Changelog or commit message explains what changed

## Sample activation prompts

- "Create a new tag for this bug fix"
- "What version should I bump for this change?"
- "Update the tag after this fix"
- "We're ready for v1.0.0 — what needs to happen?"
