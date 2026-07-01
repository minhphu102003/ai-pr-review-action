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
import sys
import time
import urllib.error
import urllib.request

HTTP_TIMEOUT = 120
REVIEW_SIGNATURE = "*AI Review by ai-pr-review-action*"


def get_env(name: str, required: bool = False) -> str:
    val = os.environ.get(name, "").strip()
    if required and not val:
        print(f"Error: {name} is required but not set", file=sys.stderr)
        sys.exit(1)
    return val


def safe_request(url: str, data: bytes | None = None, headers: dict | None = None, max_retries: int = 3) -> dict:
    """HTTP request with error handling, timeout, and retry for transient errors."""
    req = urllib.request.Request(url, data=data, headers=headers or {}, method="POST" if data else "GET")

    for attempt in range(max_retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                raw = resp.read().decode("utf-8")
                try:
                    return json.loads(raw)
                except json.JSONDecodeError:
                    print(f"Non-JSON response from {url}: {raw[:500]}", file=sys.stderr)
                    sys.exit(1)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:500]

            # Retry on 429 and 5xx with exponential backoff
            if e.code == 429 or e.code >= 500:
                if attempt < max_retries:
                    # Respect Retry-After header
                    retry_after = e.headers.get("Retry-After")
                    if retry_after and retry_after.isdigit():
                        delay = int(retry_after)
                    else:
                        delay = 2 ** (attempt + 1)  # 2, 4, 8 seconds
                    print(f"HTTP {e.code} from {url}, retrying in {delay}s (attempt {attempt + 1}/{max_retries})...", file=sys.stderr)
                    time.sleep(delay)
                    continue

            print(f"HTTP {e.code} from {url}", file=sys.stderr)
            print(f"Response: {body}", file=sys.stderr)
            if e.code == 401:
                print("Hint: Check your API key is correct and not expired.", file=sys.stderr)
            elif e.code == 403:
                print("Hint: Access denied. Check API key permissions.", file=sys.stderr)
            elif e.code == 404:
                print("Hint: Resource not found. The PR may have been closed or deleted.", file=sys.stderr)
            elif e.code == 429:
                print("Hint: Rate limited. Try again later or use a different provider.", file=sys.stderr)
            elif e.code >= 500:
                print("Hint: Server error. The API may be experiencing issues.", file=sys.stderr)
            sys.exit(1)
        except urllib.error.URLError as e:
            if attempt < max_retries:
                delay = 2 ** (attempt + 1)
                print(f"Connection error: {e.reason}, retrying in {delay}s (attempt {attempt + 1}/{max_retries})...", file=sys.stderr)
                time.sleep(delay)
                continue
            print(f"Connection error: {e.reason}", file=sys.stderr)
            print("Hint: Check your network connection and base URL.", file=sys.stderr)
            sys.exit(1)
        except TimeoutError:
            if attempt < max_retries:
                delay = 2 ** (attempt + 1)
                print(f"Request timed out after {HTTP_TIMEOUT}s, retrying in {delay}s (attempt {attempt + 1}/{max_retries})...", file=sys.stderr)
                time.sleep(delay)
                continue
            print(f"Request timed out after {HTTP_TIMEOUT}s", file=sys.stderr)
            sys.exit(1)

    # Should not reach here, but just in case
    print("All retry attempts exhausted", file=sys.stderr)
    sys.exit(1)


def detect_provider() -> tuple[str, str, str]:
    """Detect LLM provider from available API keys."""
    openai_key = get_env("OPENAI_API_KEY")
    anthropic_key = get_env("ANTHROPIC_API_KEY")

    if anthropic_key and openai_key:
        print("WARNING: Both OPENAI_API_KEY and ANTHROPIC_API_KEY set. Using Anthropic.", file=sys.stderr)

    if anthropic_key:
        model = get_env("MODEL") or "claude-haiku-4-5-20251001"
        return "anthropic", anthropic_key, model
    elif openai_key:
        model = get_env("MODEL") or "gpt-4.1-mini"
        return "openai", openai_key, model
    else:
        print("Error: Set OPENAI_API_KEY or ANTHROPIC_API_KEY", file=sys.stderr)
        sys.exit(1)


def get_github_info() -> tuple[str, str, int]:
    """Extract owner, repo, PR number from GITHUB_REPOSITORY and event."""
    repo = get_env("GITHUB_REPOSITORY", required=True)
    owner, repo_name = repo.split("/", 1)

    event_path = get_env("GITHUB_EVENT_PATH")
    if event_path:
        with open(event_path, encoding="utf-8") as f:
            event = json.load(f)
        # For issue_comment events, verify it's on a PR (not a regular issue)
        if "issue" in event and "pull_request" not in event.get("issue", {}):
            print("Comment is not on a PR. Skipping.", file=sys.stderr)
            sys.exit(0)
        pr_number = event.get("pull_request", {}).get("number") or event.get("issue", {}).get("number")
        if pr_number:
            return owner, repo_name, int(pr_number)

    print("Error: Could not determine PR number from event", file=sys.stderr)
    sys.exit(1)


def get_pr_diff(owner: str, repo: str, pr_number: int, token: str) -> str:
    """Get PR diff via GitHub API."""
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}"
    headers = {
        "Authorization": f"token {token}",
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
            "Authorization": f"token {token}",
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

    # Filter diff hunks by file path, also strip binary files
    filtered_parts = []
    skip = False
    in_header = False  # Between diff --git and first @@

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
        # Only detect binary files in the header region (before first @@)
        if in_header and line.startswith("Binary files") and "differ" in line:
            # Remove lines belonging to this binary file's diff header
            while filtered_parts and not filtered_parts[-1].startswith("diff --git"):
                filtered_parts.pop()
            if filtered_parts:
                filtered_parts.pop()  # Remove the diff --git header
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

    # Try file boundary first
    last_boundary = truncated.rfind("\ndiff --git ")
    if last_boundary > 0:
        return truncated[:last_boundary] + "\n\n... [diff truncated — too large for review]"

    # Fallback to hunk boundary
    last_hunk = truncated.rfind("\n@@")
    if last_hunk > 0:
        return truncated[:last_hunk] + "\n\n... [diff truncated — too large for review]"

    # Last resort: cut at last newline
    last_newline = truncated.rfind("\n")
    if last_newline > 0:
        return truncated[:last_newline] + "\n\n... [diff truncated — too large for review]"

    return truncated + "\n\n... [diff truncated — too large for review]"


def _build_api_url(base_url_env: str, default_url: str, path: str) -> str:
    """Build API URL from base_url, avoiding double /v1."""
    base_url = (get_env(base_url_env) or default_url).rstrip("/")
    # Avoid double /v1 if user set base_url with /v1
    if base_url.endswith("/v1"):
        base_url = base_url[:-3]
    return f"{base_url}/v1/{path}"


def call_openai(api_key: str, model: str, prompt: str, diff: str) -> str:
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
            {"role": "user", "content": f"Here is the PR diff to review:\n\n```diff\n{diff}\n```"},
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


def call_anthropic(api_key: str, model: str, prompt: str, diff: str) -> str:
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
            {"role": "user", "content": f"Here is the PR diff to review:\n\n```diff\n{diff}\n```"},
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


def find_existing_comment(owner: str, repo: str, pr_number: int, token: str) -> int | None:
    """Find existing review comment by signature. Returns comment ID or None."""
    url = f"https://api.github.com/repos/{owner}/{repo}/issues/{pr_number}/comments?per_page=100"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "ai-pr-review-action",
    }
    result = safe_request(url, headers=headers)
    for comment in result:
        if REVIEW_SIGNATURE in comment.get("body", ""):
            return comment["id"]
    return None


def update_comment(owner: str, repo: str, comment_id: int, token: str, body: str):
    """Update an existing comment."""
    url = f"https://api.github.com/repos/{owner}/{repo}/issues/comments/{comment_id}"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "ai-pr-review-action",
    }
    payload = json.dumps({"body": body}).encode("utf-8")
    result = safe_request(url, data=payload, headers=headers)
    print(f"Review comment updated: {result.get('html_url', 'ok')}")


def post_comment(owner: str, repo: str, pr_number: int, token: str, body: str, update_existing: bool = True):
    """Post or update a comment on the PR."""
    if update_existing:
        comment_id = find_existing_comment(owner, repo, pr_number, token)
        if comment_id:
            update_comment(owner, repo, comment_id, token, body)
            return
    url = f"https://api.github.com/repos/{owner}/{repo}/issues/{pr_number}/comments"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "ai-pr-review-action",
    }
    payload = json.dumps({"body": body}).encode("utf-8")
    result = safe_request(url, data=payload, headers=headers)
    print(f"Review comment posted: {result.get('html_url', 'ok')}")


def main():
    provider, api_key, model = detect_provider()
    owner, repo, pr_number = get_github_info()
    token = get_env("GITHUB_TOKEN", required=True)
    prompt = get_env("PROMPT", required=True)
    exclude = get_env("EXCLUDE")
    update_existing = get_env("UPDATE_COMMENT").lower() != "false"

    print(f"Provider: {provider}, Model: {model}")
    print(f"PR: {owner}/{repo}#{pr_number}")

    # Get diff
    diff = get_pr_diff(owner, repo, pr_number, token)
    print(f"Diff size: {len(diff)} chars")

    # Filter excluded files and binary files
    files = get_pr_files(owner, repo, pr_number, token) if exclude else []
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

    # Truncate if needed
    diff = truncate_diff(diff)

    # Call LLM
    print(f"Calling {provider} API...")
    if provider == "openai":
        review = call_openai(api_key, model, prompt, diff)
    else:
        review = call_anthropic(api_key, model, prompt, diff)

    print(f"Review length: {len(review)} chars")

    # Post comment
    post_comment(owner, repo, pr_number, token, review, update_existing=update_existing)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected error: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)
