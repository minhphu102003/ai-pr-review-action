# Changelog

## v0.2.0

- Repository Memory Rules: teach the bot your coding conventions via `.synaptic/rules.json`
- `@synaptic-ai remember:` command: add rules from PR comments (collaborators only)
- Rules auto-injected into LLM prompt for both engines (OpenCode and direct)
- Auto-commit rules.json to repo with SHA-based concurrency retry
- Collaborator verification via GitHub API before accepting rules
- Graceful fallback for read-only tokens (fork PRs) and branch protection

## v0.1.3

- Fix `has_bot_reviews` false positive: remove `body == ""` check that blocked inline comments on empty bot reviews
- Add retry logic (429/5xx with exponential backoff) to `_graphql()` — was the only HTTP call without retry
- Fix `filter_diff` early return: `and` → `or` to skip processing when no excluded files
- Fix `extract_issues_json` / `extract_replies_json` to preserve text after JSON blocks instead of discarding

## v0.1.2

- Fix shell syntax `${ 32768 }` bug in action.yml prompt size warning
- Consolidate 10+ duplicated functions from `post_inline.py` and `review_direct.py` into `review_context.py`
- Add retry logic (429/5xx with exponential backoff) to all GitHub API calls via shared `safe_request()`
- Fix `filter_threads()` fragile `startswith("[")` heuristic — now checks author login instead
- Move `::add-mask::` from module import time to explicit `mask_secrets()` call in `main()`
- Mask `GITHUB_TOKEN` in `post_inline.py` (was missing)
- Add `has_bot_reviews()` duplicate prevention to direct engine's `post_inline_comments()`
- Remove unused `summary_body` parameter from `post_inline_comments()`
- Sync context file auto-detect lists: add `README.md` to OpenCode engine path
- Move `REVIEW_SIGNATURE`, `HTTP_TIMEOUT`, `_JSON_BLOCK_PATTERN` constants to single source of truth in `review_context.py`
- Move `argparse` import to `main_cli()` to avoid unnecessary import when used as module
- Update CLAUDE.md project structure to list all three scripts

## v0.1.1

- Strip model preamble: remove leading text before `## PR Review` heading in both OpenCode and direct engine paths
- OpenCode engine: `post_inline.py` now always updates summary comment when body changes (preamble or Key Issues stripped)
- Direct engine: `sanitize_review()` strips preamble before posting

## v0.1.0

- Issue Summary table: LLM generates a summary table with severity, file, line, and description before Key Issues section
- Signature renamed to "Synaptic PR Review" with clickable marketplace link
- Prompt rewrite: increased char limit from 3000 to 8000, merged JSON blocks into output format
- Conditional sections (`<previous_review_context>`, `<user_replies>`) only included when data exists
- Bug fixes: safe_request retry logic, duplicate code block removal, has_bot_reviews precision
- Added `commit_id` to inline review payloads to prevent line drift
- Token read from env var instead of CLI arg in review_context.py

## v0.0.20

- Auto-reply to user comments: bot detects user replies to inline review comments and generates contextual responses
- LLM decides whether to reply — no regex filtering; handles debates, questions, and "fixed" replies intelligently
- Review context injection: unresolved threads passed as `<previous_review_context>` to prevent re-raising discussed issues
- GraphQL API for fetching PR review threads with client-side filtering
- Reply context formatted as `<user_replies>` block with comment IDs for structured reply generation
- Bot replies tagged with `<!-- AI_REVIEW_REPLY -->` to avoid re-processing
- Both engines (OpenCode and direct LLM) support auto-reply
- `/review` comment trigger: users can comment `/review` on a PR to trigger re-review

## v0.0.19

- Add context files support: auto-detect architecture docs, CLAUDE.md, AGENTS.md, SOUL.md, MEMORY.md, README
- User can specify context files via `context_files` input (comma-separated)
- Smart budget: context files only fetched when diff < 70K chars (15K budget)
- LLM receives context in `<context>` block alongside the diff
- Add inline review comments for OpenCode engine (post-processing step)
- Only warn for user-specified context paths, not auto-detect

## v0.0.18

- Add severity level (Critical/Warning/Suggestion) with color icons to inline comments
- Remove duplicate review body from inline review (summary comment is the single source of truth)

## v0.0.16

- Add inline resolvable review comments via PR Reviews API
- Each key issue becomes a separate inline comment on the specific file/line
- Summary comment (non-resolvable) still posted with full review content
- LLM outputs JSON block at end of review for structured issue extraction

## v0.0.15

- Enforce review comment starts with `## PR Review` — no preamble text

## v0.0.14

- Add `issues: write` permission for OpenCode engine reactions API (`/issues/{n}/reactions`)

## v0.0.13

- Revert `reactions: write` permission — not a valid GitHub Actions permission

## v0.0.12

- Add "What This PR Does" section to review output format

## v0.0.11

- Add `reactions: write` permission to README and SECURITY.md (required by OpenCode engine)

## v0.0.10

- Add "Free" to marketplace description for search discoverability
- Add zero-cost callout and OpenCode setup guide to README

## v0.0.9

- Rename to "Synaptic PR Review" for marketplace consistency
- Reorder output format: move Flow Overview diagram before Key Issues
- Remove collapsible wrapper on Mermaid diagram (expanded by default)
- Update README title and description to match marketplace listing

## v0.0.8

- Fix OpenCode action reference: `@v1` tag does not exist, pin to `@v1.17.12`
- Remove `share` input (session link feature unreliable)
- Mask API keys (including `GITHUB_TOKEN`) in GitHub Actions logs via `::add-mask::`
- Move Security section after Quick Start in README for visibility
- Add step-by-step secret setup guide to README
- Add "do not hardcode" warnings to all API key inputs in README
- Add Required Permissions table to README and SECURITY.md
- Strengthen auto-commit warning with explicit `contents: write` mention
- Fix SECURITY.md inaccurate permissions claim
- Add auto-commit risk and third-party dependency disclosure to SECURITY.md
- Update action.yml input descriptions: warn against hardcoding API keys
- Fix README: `openai_base_url` and `anthropic_base_url` defaults (empty, not full URL)
- Fix README: mark `exclude` and `update_comment` as direct-engine-only inputs
- Fix README: GPT-4.1 Mini context size (1M, not 128k)

## v0.0.7

- Validate model names via GET /v1/models for OpenAI-compatible APIs
- Suggest closest model name on typo via edit distance
- Prefix-based model check for Anthropic (no public models API)
- Add context-aware error hints for HTTP 400 and 404 (LLM vs GitHub)
- Graceful fallback if /v1/models endpoint unavailable

## v0.0.6

- Add GitHub issue templates (bug report, feature request) with YAML forms
- Add PR template with checklist
- Add template chooser config (disable blank issues, link to Discussions)
- Add SECURITY.md

## v0.0.5

- Use sequenceDiagram as default Mermaid diagram type

## v0.0.4

- Sanitize @mentions and external images in LLM output
- Use Bearer prefix for GitHub token auth
- Configurable HTTP timeout via `HTTP_TIMEOUT` env var
- Always call get_pr_files for reliable binary detection
- Exit code 130 for KeyboardInterrupt
- Remove unnecessary `id-token: write` permission from README
- Add bot comment filter to prevent review loops
- Add draft PR filter to comment-review job
- Add Cost Considerations and Fork PRs documentation

## v0.0.3

- Fix script injection in engine validation (moved to env var)
- Fix Python version requirement: 3.9+ -> 3.10+
- Fix binary detection false positive (only detect in header region)
- Fix Anthropic response parsing by content block type
- Add retry logic with exponential backoff for 429/5xx errors
- Add duplicate review prevention (update existing comment)
- Add `update_comment` input (default: true)
- Validate issue_comment events are on PRs
- Warn on 300-file diff limit from GitHub API
- Prompt file extension validation
- Prompt size validation (>32KB warning)
- Dynamic heredoc delimiter based on GITHUB_RUN_ID
- Pin OpenCode action to @v1 instead of @latest
- Pagination max page limit (20 pages / 2000 files)

## v0.0.2

- Fix filter_diff binary file handling that could corrupt previous file content
- Exit on get_pr_files pagination errors instead of returning partial results
- Validate LLM response structure before accessing nested fields
- Handle non-JSON API responses (e.g. Cloudflare HTML error pages)
- Add 404 hint for closed/deleted PRs
- Add top-level exception handler to prevent raw tracebacks in logs
- Add CLAUDE.md project instructions
- Add version convention skill

## v0.0.1

- Initial release
- Dual engine support: OpenCode and direct LLM API (OpenAI, Anthropic)
- Custom base URL support for compatible providers (OpenRouter, Groq, Together AI, Ollama)
- Built-in review prompt with anti-hallucination rules
- GitHub callout syntax for severity (CAUTION, WARNING, NOTE)
- Mermaid diagram generation
- File exclusion via glob patterns
- Binary file detection and stripping
- Diff truncation at file boundaries
