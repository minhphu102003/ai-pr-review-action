#!/usr/bin/env python3
"""
Direct LLM API engine for AI PR Review.
Supports OpenAI and Anthropic APIs.
Uses only stdlib (urllib, json, os, sys, fnmatch) — no pip install needed.
"""

import fnmatch
import json
import os
import sys
import urllib.error
import urllib.request


def get_env(name: str, required: bool = False) -> str:
    val = os.environ.get(name, "").strip()
    if required and not val:
        print(f"Error: {name} is required but not set", file=sys.stderr)
        sys.exit(1)
    return val


def detect_provider() -> tuple[str, str]:
    """Detect LLM provider from available API keys. Returns (provider, api_key)."""
    openai_key = get_env("OPENAI_API_KEY")
    anthropic_key = get_env("ANTHROPIC_API_KEY")

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
    with urllib.request.urlopen(req) as resp:
        return resp.read().decode("utf-8")


def get_pr_files(owner: str, repo: str, pr_number: int, token: str) -> list[dict]:
    """Get list of changed files in PR."""
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/files?per_page=100"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "ai-pr-review-action",
    }
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))


def filter_diff(diff: str, files: list[dict], exclude_patterns: str) -> str:
    """Filter out excluded files from diff."""
    if not exclude_patterns:
        return diff

    patterns = [p.strip() for p in exclude_patterns.split(",") if p.strip()]
    if not patterns:
        return diff

    excluded_files = set()
    for f in files:
        filename = f.get("filename", "")
        for pattern in patterns:
            if fnmatch.fnmatch(filename, pattern):
                excluded_files.add(filename)
                break

    if not excluded_files:
        return diff

    # Filter diff hunks by file path
    filtered_parts = []
    current_file = None
    skip = False

    for line in diff.split("\n"):
        if line.startswith("diff --git"):
            # Extract filename from "diff --git a/path b/path"
            parts = line.split(" b/", 1)
            current_file = parts[1] if len(parts) > 1 else ""
            skip = current_file in excluded_files
        if not skip:
            filtered_parts.append(line)

    return "\n".join(filtered_parts)


def truncate_diff(diff: str, max_chars: int = 100000) -> str:
    """Truncate diff if too large for LLM context."""
    if len(diff) <= max_chars:
        return diff
    return diff[:max_chars] + "\n\n... [diff truncated — too large for review]"


def call_openai(api_key: str, model: str, prompt: str, diff: str) -> str:
    """Call OpenAI API."""
    url = "https://api.openai.com/v1/chat/completions"
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

    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    return result["choices"][0]["message"]["content"]


def call_anthropic(api_key: str, model: str, prompt: str, diff: str) -> str:
    """Call Anthropic API."""
    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    body = json.dumps({
        "model": model,
        "max_tokens": 4096,
        "system": prompt,
        "messages": [
            {"role": "user", "content": f"Here is the PR diff to review:\n\n```diff\n{diff}\n```"},
        ],
    }).encode("utf-8")

    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read().decode("utf-8"))
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
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read().decode("utf-8"))
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

    # Filter excluded files
    if exclude:
        files = get_pr_files(owner, repo, pr_number, token)
        diff = filter_diff(diff, files, exclude)
        print(f"Diff size after exclusions: {len(diff)} chars")

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
