#!/usr/bin/env python3
"""
Direct LLM API engine for AI PR Review.
Supports OpenAI and Anthropic APIs.
Uses only stdlib (urllib, json, os, sys, fnmatch) — no pip install needed.
Requires Python 3.10+.
"""

import fnmatch
import json
import os
import re
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from review_context import (
    HTTP_TIMEOUT, REVIEW_SIGNATURE,
    extract_issues_json, extract_replies_json,
    fetch_unresolved_threads, filter_threads, find_user_replies,
    format_reply_context, format_review_context, get_env, get_github_info,
    get_latest_commit, mask_secrets, post_inline_comments, post_reply,
    safe_request, update_comment, find_existing_comment,
)

# Known model prefixes for Anthropic (no public models API).
# OpenAI-compatible APIs are validated via GET /v1/models instead.
_ANTHROPIC_PREFIXES = ("claude-",)


def sanitize_review(text: str) -> str:
    """Sanitize LLM output before posting as GitHub comment."""
    # Strip everything before ## PR Review (model preamble/leading text)
    idx = text.find("## PR Review")
    if idx > 0:
        text = text[idx:]
    # Strip @mentions to prevent unintended notifications
    text = re.sub(r'(?<!\w)@(\w+)', r'`\@\1`', text)
    # Strip markdown image tags with external URLs (tracking pixels)
    text = re.sub(r'!\[([^\]]*)\]\(https?://[^\)]+\)', r'[image: \1]', text)
    return text


def _validate_model(provider: str, model: str, api_key: str) -> None:
    """Validate that the model exists. Exits on failure for OpenAI, warns for Anthropic."""
    if provider == "openai":
        _validate_openai_model(model, api_key)
    elif provider == "anthropic":
        _validate_anthropic_model(model)


def _validate_openai_model(model: str, api_key: str) -> None:
    """Query GET /v1/models to verify the model exists. Works for OpenAI and compatible APIs."""
    url = _build_api_url("OPENAI_BASE_URL", "https://api.openai.com", "models")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    }
    try:
        req = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        available = {m["id"] for m in data.get("data", [])}
        if model not in available:
            suggestion = _closest_model(model, available)
            msg = f"Error: Model '{model}' not found."
            if suggestion:
                msg += f" Did you mean '{suggestion}'?"
            print(msg, file=sys.stderr)
            print(f"Available models: {', '.join(sorted(available)[:20])}{'...' if len(available) > 20 else ''}", file=sys.stderr)
            sys.exit(1)
        print(f"Model '{model}' verified.", file=sys.stderr)
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, TimeoutError) as e:
        print(f"WARNING: Could not verify model '{model}' ({type(e).__name__}). Proceeding anyway.", file=sys.stderr)


def _validate_anthropic_model(model: str) -> None:
    """Prefix-based check for Anthropic models (no public models API)."""
    if not model.startswith(_ANTHROPIC_PREFIXES):
        print(
            f"WARNING: Model '{model}' may not be a valid Anthropic model. "
            f"Expected prefix: {', '.join(_ANTHROPIC_PREFIXES)}",
            file=sys.stderr,
        )


def _closest_model(target: str, candidates: set[str]) -> str | None:
    """Find the closest model name using edit distance. Returns None if no close match."""
    def _edit_distance(a: str, b: str) -> int:
        m, n = len(a), len(b)
        dp = list(range(n + 1))
        for i in range(1, m + 1):
            prev = dp[0]
            dp[0] = i
            for j in range(1, n + 1):
                temp = dp[j]
                if a[i - 1] == b[j - 1]:
                    dp[j] = prev
                else:
                    dp[j] = 1 + min(prev, dp[j], dp[j - 1])
                prev = temp
        return dp[n]

    best, best_dist = None, len(target)
    for c in candidates:
        d = _edit_distance(target, c)
        if d < best_dist:
            best, best_dist = c, d
    return best


def detect_provider() -> tuple[str, str, str]:
    """Detect LLM provider from available API keys."""
    openai_key = get_env("OPENAI_API_KEY")
    anthropic_key = get_env("ANTHROPIC_API_KEY")
    model_env = get_env("MODEL")

    if anthropic_key and openai_key:
        if model_env and model_env.startswith(_ANTHROPIC_PREFIXES):
            model = model_env
            print(f"WARNING: Both keys set. Model '{model}' matches Anthropic.", file=sys.stderr)
            _validate_model("anthropic", model, anthropic_key)
            return "anthropic", anthropic_key, model
        else:
            model = model_env or "gpt-4.1-mini"
            print(f"WARNING: Both keys set. Model '{model}' uses OpenAI.", file=sys.stderr)
            _validate_model("openai", model, openai_key)
            return "openai", openai_key, model
    elif anthropic_key:
        model = model_env or "claude-haiku-4-5-20251001"
        _validate_model("anthropic", model, anthropic_key)
        return "anthropic", anthropic_key, model
    elif openai_key:
        model = model_env or "gpt-4.1-mini"
        _validate_model("openai", model, openai_key)
        return "openai", openai_key, model
    else:
        print("Error: Set OPENAI_API_KEY or ANTHROPIC_API_KEY", file=sys.stderr)
        sys.exit(1)


def get_pr_diff(owner: str, repo: str, pr_number: int, token: str) -> str:
    """Get PR diff via GitHub API."""
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3.diff",
        "User-Agent": "ai-pr-review-action",
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code} getting PR diff: {e.reason}", file=sys.stderr)
        if e.code == 404:
            print("Hint: PR not found. Check repository and PR number.", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"Connection error getting PR diff: {e.reason}", file=sys.stderr)
        sys.exit(1)


def get_pr_files(owner: str, repo: str, pr_number: int, token: str) -> list[dict]:
    """Get list of changed files in PR (handles pagination)."""
    all_files = []
    page = 1
    max_pages = 20  # 2000 files max
    while True:
        if page > max_pages:
            print(f"WARNING: Exceeded {max_pages} pages of files. Stopping pagination.", file=sys.stderr)
            break
        url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/files?per_page=100&page={page}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "ai-pr-review-action",
        }
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                files = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            print(f"HTTP {e.code} getting PR files: {e.reason}", file=sys.stderr)
            sys.exit(1)
        except urllib.error.URLError as e:
            print(f"Connection error getting PR files: {e.reason}", file=sys.stderr)
            sys.exit(1)

        if not files:
            break
        all_files.extend(files)
        if len(files) < 100:
            break
        page += 1

    return all_files


# Context file budget: max 15K chars for architecture/spec files
_CONTEXT_MAX_CHARS = 15000
# Only fetch context files when diff is smaller than this
_DIFF_THRESHOLD_FOR_CONTEXT = 70000

# Files to auto-detect when user doesn't specify context_files
_AUTO_CONTEXT_PATHS = [
    "CLAUDE.md",
    "AGENTS.md",
    "SOUL.md",
    "MEMORY.md",
    "docs/architecture.md",
    "docs/ARCHITECTURE.md",
    "ARCHITECTURE.md",
    "README.md",
]


def _fetch_file_content(owner: str, repo: str, path: str, token: str, warn: bool = False) -> str | None:
    """Fetch a single file's raw content from GitHub. Returns None on error."""
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3.raw",
        "User-Agent": "ai-pr-review-action",
    }
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        if warn:
            if e.code == 404:
                print(f"WARNING: Context file not found: {path}", file=sys.stderr)
            else:
                print(f"WARNING: Failed to fetch context file {path}: HTTP {e.code}", file=sys.stderr)
        return None
    except urllib.error.URLError as e:
        if warn:
            print(f"WARNING: Failed to fetch context file {path}: {e.reason}", file=sys.stderr)
        return None


def fetch_context_files(
    owner: str, repo: str, token: str, context_files_input: str | None
) -> str:
    """Fetch context files from repo for LLM context.

    Priority:
    1. User-specified files (context_files input, comma-separated)
    2. Auto-detect: CLAUDE.md, architecture docs, README

    Returns concatenated file content, truncated to _CONTEXT_MAX_CHARS.
    """
    if context_files_input:
        paths_to_fetch = [p.strip() for p in context_files_input.split(",") if p.strip()]
    else:
        paths_to_fetch = list(_AUTO_CONTEXT_PATHS)

    is_user_specified = bool(context_files_input)
    paths = []
    for p in paths_to_fetch:
        content = _fetch_file_content(owner, repo, p, token, warn=is_user_specified)
        if content is not None:
            paths.append((p, content))

    parts = []
    total_chars = 0

    for path, content in paths:
        if total_chars >= _CONTEXT_MAX_CHARS:
            break

        # Truncate README to first 2000 chars
        if path.upper().endswith("README.md") and len(content) > 2000:
            content = content[:2000] + "\n... [truncated]"

        remaining = _CONTEXT_MAX_CHARS - total_chars
        if len(content) > remaining:
            content = content[:remaining] + "\n... [truncated]"

        parts.append(f"--- {path} ---\n{content}")
        total_chars += len(content) + len(path) + 10

    if parts:
        print(f"Context files: {len(parts)} file(s), {total_chars} chars")
    return "\n\n".join(parts)


def filter_diff(diff: str, files: list[dict], exclude_patterns: str) -> str:
    """Filter out excluded files and binary files from diff."""
    if not exclude_patterns:
        exclude_patterns = ""

    patterns = [p.strip() for p in exclude_patterns.split(",") if p.strip()]
    excluded_files = set()

    if patterns:
        for f in files:
            filename = f.get("filename", "")
            for pattern in patterns:
                if fnmatch.fnmatch(filename, pattern):
                    excluded_files.add(filename)
                    break

    if not excluded_files and not diff:
        return diff

    filtered_parts = []
    skip = False
    in_header = False

    for line in diff.split("\n"):
        if line.startswith("diff --git"):
            parts = line.split(" b/", 1)
            current_file = parts[1] if len(parts) > 1 else ""
            skip = current_file in excluded_files
            in_header = True
            if skip:
                continue
            filtered_parts.append(line)
            continue
        if skip:
            continue
        if in_header and line.startswith("Binary files") and "differ" in line:
            while filtered_parts and not filtered_parts[-1].startswith("diff --git"):
                filtered_parts.pop()
            if filtered_parts:
                filtered_parts.pop()
            in_header = False
            continue
        if line.startswith("@@"):
            in_header = False
        filtered_parts.append(line)

    return "\n".join(filtered_parts)


def truncate_diff(diff: str, max_chars: int = 100000) -> str:
    """Truncate diff at a file or hunk boundary if too large."""
    if len(diff) <= max_chars:
        return diff

    truncated = diff[:max_chars]

    last_boundary = truncated.rfind("\ndiff --git ")
    if last_boundary > 0:
        return truncated[:last_boundary] + "\n\n... [diff truncated — too large for review]"

    last_hunk = truncated.rfind("\n@@")
    if last_hunk > 0:
        return truncated[:last_hunk] + "\n\n... [diff truncated — too large for review]"

    last_newline = truncated.rfind("\n")
    if last_newline > 0:
        return truncated[:last_newline] + "\n\n... [diff truncated — too large for review]"

    return truncated + "\n\n... [diff truncated — too large for review]"


def _build_api_url(base_url_env: str, default_url: str, path: str) -> str:
    """Build API URL from base_url, avoiding double /v1."""
    base_url = (get_env(base_url_env) or default_url).rstrip("/")
    if base_url.endswith("/v1"):
        base_url = base_url[:-3]
    return f"{base_url}/v1/{path}"


def _build_user_message(diff: str, context: str = "", review_context: str = "", reply_context: str = "") -> str:
    """Build the user message with optional context, review context, and reply context."""
    parts = []
    if context:
        parts.append(f"<context>\n{context}\n</context>")
    if review_context:
        parts.append(review_context)
    if reply_context:
        parts.append(reply_context)
    parts.append(f"<diff>\n```diff\n{diff}\n```\n</diff>")
    return "\n\n".join(parts)


def call_openai(api_key: str, model: str, prompt: str, diff: str, context: str = "",
                review_context: str = "", reply_context: str = "") -> str:
    """Call OpenAI or OpenAI-compatible API."""
    url = _build_api_url("OPENAI_BASE_URL", "https://api.openai.com", "chat/completions")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": _build_user_message(diff, context, review_context, reply_context)},
        ],
        "max_tokens": 4096,
        "temperature": 0.3,
    }).encode("utf-8")

    result = safe_request(url, data=body, headers=headers)
    if "error" in result:
        print(f"OpenAI API error: {result['error']}", file=sys.stderr)
        sys.exit(1)
    if "choices" not in result or not result["choices"]:
        print(f"Unexpected OpenAI response: {json.dumps(result)[:500]}", file=sys.stderr)
        sys.exit(1)
    return result["choices"][0]["message"]["content"]


def call_anthropic(api_key: str, model: str, prompt: str, diff: str, context: str = "",
                   review_context: str = "", reply_context: str = "") -> str:
    """Call Anthropic or Anthropic-compatible API."""
    url = _build_api_url("ANTHROPIC_BASE_URL", "https://api.anthropic.com", "messages")

    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    body = json.dumps({
        "model": model,
        "max_tokens": 4096,
        "temperature": 0.3,
        "system": prompt,
        "messages": [
            {"role": "user", "content": _build_user_message(diff, context, review_context, reply_context)},
        ],
    }).encode("utf-8")

    result = safe_request(url, data=body, headers=headers)
    if "error" in result:
        print(f"Anthropic API error: {result['error']}", file=sys.stderr)
        sys.exit(1)
    if "content" not in result or not result["content"]:
        print(f"Unexpected Anthropic response: {json.dumps(result)[:500]}", file=sys.stderr)
        sys.exit(1)
    for block in result["content"]:
        if block.get("type") == "text":
            return block["text"]
    print("No text content in Anthropic response", file=sys.stderr)
    sys.exit(1)


def post_comment(owner: str, repo: str, pr_number: int, token: str, body: str, update_existing: bool = True):
    """Post or update a comment on the PR."""
    if update_existing:
        comment_id = find_existing_comment(owner, repo, pr_number, token)
        if comment_id:
            update_comment(owner, repo, comment_id, token, body)
            return
    url = f"https://api.github.com/repos/{owner}/{repo}/issues/{pr_number}/comments"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "ai-pr-review-action",
    }
    payload = json.dumps({"body": body}).encode("utf-8")
    result = safe_request(url, data=payload, headers=headers)
    print(f"Review comment posted: {result.get('html_url', 'ok')} (id: {result.get('id', '?')})")


def main():
    # Mask API keys in GitHub Actions logs
    mask_secrets()

    # detect_provider() validates API keys and model name
    provider, api_key, model = detect_provider()
    owner, repo, pr_number = get_github_info()
    token = get_env("GITHUB_TOKEN", required=True)
    prompt = get_env("PROMPT", required=True)
    exclude = get_env("EXCLUDE")
    update_existing = get_env("UPDATE_COMMENT").lower() != "false"
    context_files_input = get_env("CONTEXT_FILES")

    print(f"Provider: {provider}, Model: {model}")
    print(f"PR: {owner}/{repo}#{pr_number}")

    # Get diff
    diff = get_pr_diff(owner, repo, pr_number, token)
    print(f"Diff size: {len(diff)} chars")

    # Filter excluded files and binary files
    files = get_pr_files(owner, repo, pr_number, token)
    if exclude and not files:
        print("WARNING: exclude patterns set but no files returned from API. Patterns may not apply.", file=sys.stderr)
    diff = filter_diff(diff, files, exclude)
    print(f"Diff size after filtering: {len(diff)} chars")

    # Warn if diff may be truncated by GitHub's 300-file limit
    if files:
        diff_file_count = diff.count("\ndiff --git ")
        if len(files) > diff_file_count:
            print(f"WARNING: GitHub API returned diff for {diff_file_count} of {len(files)} files. PR may be too large for complete review.", file=sys.stderr)

    # Skip if no reviewable changes
    if not diff.strip():
        print("No reviewable changes found. Skipping LLM call.")
        post_comment(owner, repo, pr_number, token,
                     "> [!NOTE]\n> No reviewable changes found in this PR.",
                     update_existing=update_existing)
        return

    # Fetch context files if diff is small enough
    context = ""
    diff_max = 100000
    if len(diff) < _DIFF_THRESHOLD_FOR_CONTEXT:
        context = fetch_context_files(owner, repo, token, context_files_input)
        if context:
            diff_max = 70000

    # Fetch unresolved review threads for context and auto-reply
    review_context = ""
    reply_context = ""
    replies_needed = []
    try:
        threads = fetch_unresolved_threads(owner, repo, pr_number, token)
        threads = filter_threads(threads)
        if threads:
            review_context = format_review_context(threads)
            replies_needed = find_user_replies(threads)
            if replies_needed:
                reply_context = format_reply_context(replies_needed)
                print(f"Found {len(replies_needed)} user reply(ies) to address")
            print(f"Review context: {len(threads)} unresolved thread(s)")
    except Exception as e:
        print(f"WARNING: Could not fetch review context: {e}", file=sys.stderr)

    # Adjust diff budget for additional context
    total_context_len = len(context) + len(review_context) + len(reply_context)
    if total_context_len > 0:
        diff_max = max(diff_max - len(review_context) - len(reply_context), 50000)

    # Truncate diff
    diff = truncate_diff(diff, max_chars=diff_max)

    # Call LLM
    print(f"Calling {provider} API...")
    if provider == "openai":
        review = call_openai(api_key, model, prompt, diff, context, review_context, reply_context)
    else:
        review = call_anthropic(api_key, model, prompt, diff, context, review_context, reply_context)

    print(f"Review length: {len(review)} chars")

    # Sanitize and extract issues
    review = sanitize_review(review)
    clean_text, issues = extract_issues_json(review)

    # Extract and post replies to user comments
    clean_text, reply_list = extract_replies_json(clean_text)
    if reply_list and replies_needed:
        valid_ids = {r["comment_id"] for r in replies_needed}
        for reply in reply_list:
            cid = reply.get("comment_id")
            body = reply.get("body", "")
            if cid in valid_ids and body:
                try:
                    post_reply(owner, repo, pr_number, cid, body, token)
                    print(f"Replied to comment {cid}")
                except Exception as e:
                    print(f"WARNING: Failed to reply to comment {cid}: {e}", file=sys.stderr)

    # Post summary comment (non-resolvable, full review)
    post_comment(owner, repo, pr_number, token, clean_text, update_existing=update_existing)

    # Post inline comments (resolvable)
    if issues:
        commit_sha = get_latest_commit(owner, repo, pr_number, token)
        if commit_sha:
            success = post_inline_comments(
                owner, repo, pr_number, token, issues, commit_sha,
            )
            if not success:
                print("Inline review failed. Summary comment was still posted.", file=sys.stderr)
        else:
            print("WARNING: Could not get commit SHA for inline review.", file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:
        print(f"Unexpected error: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)
