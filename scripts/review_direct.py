#!/usr/bin/env python3
"""
Direct LLM API engine for AI PR Review.
Supports OpenAI and Anthropic APIs.
Uses only stdlib (urllib, json, os, sys, fnmatch) — no pip install needed.
Requires Python 3.9+.
"""

import fnmatch
import json
import os
import sys
import urllib.error
import urllib.request

HTTP_TIMEOUT = 120


def get_env(name: str, required: bool = False) -> str:
    val = os.environ.get(name, "").strip()
    if required and not val:
        print(f"Error: {name} is required but not set", file=sys.stderr)
        sys.exit(1)
    return val


def safe_request(url: str, data: bytes | None = None, headers: dict | None = None) -> dict:
    """HTTP request with error handling and timeout."""
    req = urllib.request.Request(url, data=data, headers=headers or {}, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:500]
        print(f"HTTP {e.code} from {url}", file=sys.stderr)
        print(f"Response: {body}", file=sys.stderr)
        if e.code == 401:
            print("Hint: Check your API key is correct and not expired.", file=sys.stderr)
        elif e.code == 403:
            print("Hint: Access denied. Check API key permissions.", file=sys.stderr)
        elif e.code == 429:
            print("Hint: Rate limited. Try again later or use a different provider.", file=sys.stderr)
        elif e.code >= 500:
            print("Hint: Server error. The API may be experiencing issues.", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"Connection error: {e.reason}", file=sys.stderr)
        print("Hint: Check your network connection and base URL.", file=sys.stderr)
        sys.exit(1)
    except TimeoutError:
        print(f"Request timed out after {HTTP_TIMEOUT}s", file=sys.stderr)
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
    while True:
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
            break
        except urllib.error.URLError as e:
            print(f"Connection error getting PR files: {e.reason}", file=sys.stderr)
            break

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

    for line in diff.split("\n"):
        if line.startswith("diff --git"):
            parts = line.split(" b/", 1)
            current_file = parts[1] if len(parts) > 1 else ""
            skip = current_file in excluded_files
        if skip:
            continue
        # Skip binary file diffs
        if line.startswith("Binary files") and "differ" in line:
            # Remove the last file header we added
            while filtered_parts and filtered_parts[-1].startswith("diff --git"):
                filtered_parts.pop()
                # Also remove subsequent lines for this file header
            while filtered_parts and not filtered_parts[-1].startswith("diff --git"):
                filtered_parts.pop()
            continue
        filtered_parts.append(line)

    return "\n".join(filtered_parts)


def truncate_diff(diff: str, max_chars: int = 100000) -> str:
    """Truncate diff at a file boundary if too large."""
    if len(diff) <= max_chars:
        return diff

    # Find last "diff --git" boundary before max_chars
    truncated = diff[:max_chars]
    last_boundary = truncated.rfind("\ndiff --git ")
    if last_boundary > 0:
        truncated = truncated[:last_boundary]

    return truncated + "\n\n... [diff truncated — too large for review]"


def call_openai(api_key: str, model: str, prompt: str, diff: str) -> str:
    """Call OpenAI or OpenAI-compatible API."""
    base_url = get_env("OPENAI_BASE_URL") or "https://api.openai.com/v1"
    base_url = base_url.rstrip("/")
    # Avoid double /v1 if user set base_url with /v1
    if base_url.endswith("/v1"):
        base_url = base_url[:-3]
    url = f"{base_url}/v1/chat/completions"

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
    return result["choices"][0]["message"]["content"]


def call_anthropic(api_key: str, model: str, prompt: str, diff: str) -> str:
    """Call Anthropic or Anthropic-compatible API."""
    base_url = get_env("ANTHROPIC_BASE_URL") or "https://api.anthropic.com"
    base_url = base_url.rstrip("/")
    # Avoid double /v1 if user set base_url with /v1
    if base_url.endswith("/v1"):
        base_url = base_url[:-3]
    url = f"{base_url}/v1/messages"

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
    return result["content"][0]["text"]


def post_comment(owner: str, repo: str, pr_number: int, token: str, body: str):
    """Post a comment on the PR."""
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

    print(f"Provider: {provider}, Model: {model}")
    print(f"PR: {owner}/{repo}#{pr_number}")

    # Get diff
    diff = get_pr_diff(owner, repo, pr_number, token)
    print(f"Diff size: {len(diff)} chars")

    # Filter excluded files and binary files
    files = get_pr_files(owner, repo, pr_number, token) if exclude else []
    diff = filter_diff(diff, files, exclude)
    print(f"Diff size after filtering: {len(diff)} chars")

    # Skip if no reviewable changes
    if not diff.strip():
        print("No reviewable changes found. Skipping LLM call.")
        post_comment(owner, repo, pr_number, token,
                     "> [!NOTE]\n> No reviewable changes found in this PR.")
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
    post_comment(owner, repo, pr_number, token, review)


if __name__ == "__main__":
    main()
