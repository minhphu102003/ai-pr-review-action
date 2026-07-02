#!/usr/bin/env python3
"""
Shared utilities for AI PR Review action.
Contains GitHub API helpers, review processing, and context formatting.
Stdlib only — no pip dependencies. Python 3.10+.
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

HTTP_TIMEOUT = int(os.environ.get("HTTP_TIMEOUT", "120"))
GRAPHQL_URL = "https://api.github.com/graphql"
REVIEW_SIGNATURE = "Synaptic PR Review"
REPLY_SIGNATURE = "<!-- AI_REVIEW_REPLY -->"

_JSON_BLOCK_PATTERN = re.compile(
    r"<!--\s*REVIEW_ISSUES_JSON\s*\n(.*?)\n\s*-->",
    re.DOTALL,
)

_REPLIES_PATTERN = re.compile(
    r"<!--\s*REVIEW_REPLIES_JSON\s*\n(.*?)\n\s*-->",
    re.DOTALL,
)

_GRAPHQL_QUERY = """
query($owner: String!, $repo: String!, $pr: Int!, $after: String) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $pr) {
      reviewThreads(first: 50, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id isResolved isOutdated path line
          comments(first: 20) {
            nodes {
              id databaseId body author { login } createdAt path line
            }
          }
        }
      }
    }
  }
}
"""


# ---------------------------------------------------------------------------
# Secrets masking
# ---------------------------------------------------------------------------

def mask_secrets():
    """Mask API keys in GitHub Actions logs. Call once at startup."""
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GITHUB_TOKEN"):
        val = os.environ.get(key, "")
        if val:
            print(f"::add-mask::{val}")


# ---------------------------------------------------------------------------
# Environment / GitHub info
# ---------------------------------------------------------------------------

def get_env(name: str, required: bool = False) -> str:
    val = os.environ.get(name, "").strip()
    if required and not val:
        print(f"Error: {name} environment variable not set", file=sys.stderr)
        sys.exit(1)
    return val


def get_github_info() -> tuple[str, str, int]:
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


# ---------------------------------------------------------------------------
# HTTP request with retry
# ---------------------------------------------------------------------------

def safe_request(url: str, data: bytes | None = None, headers: dict | None = None, method: str | None = None, max_retries: int = 3) -> dict:
    """HTTP request with retry for 429/5xx and exponential backoff."""
    if method is None:
        method = "POST" if data else "GET"

    for attempt in range(max_retries + 1):
        req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                raw = resp.read().decode("utf-8")
                try:
                    return json.loads(raw)
                except json.JSONDecodeError:
                    print(f"Non-JSON response from {url}: {raw[:500]}", file=sys.stderr)
                    raise
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:500]

            # Retry on 429 and 5xx with exponential backoff
            if e.code == 429 or e.code >= 500:
                if attempt < max_retries:
                    retry_after = e.headers.get("Retry-After")
                    if retry_after and retry_after.isdigit():
                        delay = int(retry_after)
                    else:
                        delay = 2 ** (attempt + 1)
                    print(f"HTTP {e.code} from {url}, retrying in {delay}s (attempt {attempt + 1}/{max_retries})...", file=sys.stderr)
                    time.sleep(delay)
                    continue

            print(f"HTTP {e.code} from {url}", file=sys.stderr)
            print(f"Response: {body}", file=sys.stderr)
            raise
        except urllib.error.URLError as e:
            if attempt < max_retries:
                delay = 2 ** (attempt + 1)
                print(f"Connection error: {e.reason}, retrying in {delay}s (attempt {attempt + 1}/{max_retries})...", file=sys.stderr)
                time.sleep(delay)
                continue
            print(f"Connection error: {e.reason}", file=sys.stderr)
            raise
        except TimeoutError:
            if attempt < max_retries:
                delay = 2 ** (attempt + 1)
                print(f"Request timed out after {HTTP_TIMEOUT}s, retrying in {delay}s (attempt {attempt + 1}/{max_retries})...", file=sys.stderr)
                time.sleep(delay)
                continue
            print(f"Request timed out after {HTTP_TIMEOUT}s", file=sys.stderr)
            raise

    print("All retry attempts exhausted", file=sys.stderr)
    raise RuntimeError(f"All {max_retries} retry attempts exhausted for {url}")


# ---------------------------------------------------------------------------
# GraphQL
# ---------------------------------------------------------------------------

def _graphql(token: str, query: str, variables: dict) -> dict:
    """Execute a GraphQL query against the GitHub API."""
    payload = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "ai-pr-review-action",
    }
    req = urllib.request.Request(GRAPHQL_URL, data=payload, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    if "errors" in result:
        raise RuntimeError(f"GraphQL errors: {json.dumps(result['errors'])}")
    return result


def fetch_unresolved_threads(owner: str, repo: str, pr_number: int, token: str) -> list[dict]:
    """Fetch all unresolved review threads via GraphQL with pagination."""
    threads = []
    after = None
    for _ in range(4):  # max 4 pages x 50 = 200 threads
        variables = {"owner": owner, "repo": repo, "pr": pr_number, "after": after}
        result = _graphql(token, _GRAPHQL_QUERY, variables)
        pr_data = result["data"]["repository"]["pullRequest"]
        thread_conn = pr_data["reviewThreads"]
        for node in thread_conn["nodes"]:
            if node["isResolved"]:
                continue
            comments = [
                {
                    "id": c["id"],
                    "database_id": c["databaseId"],
                    "body": c["body"],
                    "author": c["author"]["login"],
                    "created_at": c["createdAt"],
                    "path": c["path"],
                    "line": c["line"],
                }
                for c in node["comments"]["nodes"]
            ]
            threads.append({
                "id": node["id"],
                "is_resolved": node["isResolved"],
                "is_outdated": node["isOutdated"],
                "path": node["path"],
                "line": node["line"],
                "comments": comments,
            })
        if not thread_conn["pageInfo"]["hasNextPage"]:
            break
        after = thread_conn["pageInfo"]["endCursor"]
    return threads


# ---------------------------------------------------------------------------
# Thread filtering and reply detection
# ---------------------------------------------------------------------------

def filter_threads(threads: list[dict]) -> list[dict]:
    """Remove outdated threads and threads with no user replies."""
    filtered = []
    for t in threads:
        if t["is_outdated"]:
            continue
        # Check author login instead of fragile startswith("[") heuristic
        has_user_reply = any(
            c["author"] != "github-actions[bot]" and REPLY_SIGNATURE not in c["body"]
            for c in t["comments"][1:]  # skip first comment (bot's inline comment)
        )
        if has_user_reply:
            filtered.append(t)
    return filtered


def find_user_replies(threads: list[dict]) -> list[dict]:
    """Find threads where user replied to bot's comment.

    Returns ALL user replies — LLM decides whether to reply or not.
    """
    replies = []
    for thread in threads:
        comments = thread["comments"]
        if len(comments) < 2:
            continue

        bot_comment = comments[0]
        user_replies = [c for c in comments[1:] if REPLY_SIGNATURE not in c["body"]]

        if not user_replies:
            continue

        replies.append({
            "thread_id": thread["id"],
            "comment_id": bot_comment["database_id"],
            "path": thread["path"],
            "line": thread["line"],
            "bot_comment": bot_comment["body"][:500],
            "user_replies": [
                {"author": r["author"], "body": r["body"][:500]}
                for r in user_replies
            ],
        })
    return replies


# ---------------------------------------------------------------------------
# Context formatting for LLM prompts
# ---------------------------------------------------------------------------

def format_reply_context(replies: list[dict], max_chars: int = 4000) -> str:
    """Format user replies as context for LLM to generate responses."""
    if not replies:
        return ""

    lines = [
        "<user_replies>",
        "The user replied to your previous review comments. You decide whether to reply:",
        "- If user says 'fixed' or 'done' → no reply needed, skip",
        "- If user is debating or asking questions → generate a reply",
        "- CRITICAL: Put ALL replies in REVIEW_REPLIES_JSON block, NOT in the review text",
        "",
    ]
    total = len("\n".join(lines))

    for r in replies:
        entry = [
            f"--- `{r['path']}` line {r['line']} (comment_id: {r['comment_id']}) ---",
            f"Your comment: {r['bot_comment'][:200]}",
        ]
        for ur in r["user_replies"]:
            entry.append(f"  [{ur['author']}]: {ur['body'][:200]}")
        entry.append("")
        entry_str = "\n".join(entry)
        if total + len(entry_str) > max_chars:
            lines.append(f"... and {len(replies) - replies.index(r)} more thread(s) omitted")
            break
        lines.append(entry_str)
        total += len(entry_str)

    lines.append("</user_replies>")
    return "\n".join(lines)


def format_review_context(threads: list[dict], max_chars: int = 8000) -> str:
    """Format unresolved threads as context to prevent re-raising discussed issues."""
    if not threads:
        return ""

    lines = [
        "<previous_review_context>",
        "Unresolved review threads from prior passes. Do NOT re-raise discussed issues.",
        "- If the author explained why the code is correct, trust their reasoning unless you can prove a concrete defect",
        "- If the code has been changed since the discussion, you MAY re-evaluate",
        "",
    ]
    total = len("\n".join(lines))

    for t in threads:
        comments = t["comments"]
        entry = [f"--- `{t['path']}` line {t['line']} ---"]
        for c in comments:
            author_label = "[bot]" if c == comments[0] else f"[{c['author']}]"
            body = c["body"][:300]
            entry.append(f"  {author_label} {body}")
        entry.append("")
        entry_str = "\n".join(entry)
        if total + len(entry_str) > max_chars:
            remaining = len(threads) - threads.index(t)
            lines.append(f"... and {remaining} more thread(s) omitted")
            break
        lines.append(entry_str)
        total += len(entry_str)

    lines.append("</previous_review_context>")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# GitHub API: comments and reviews
# ---------------------------------------------------------------------------

def post_reply(owner: str, repo: str, pr_number: int, comment_id: int, body: str, token: str) -> dict:
    """Post a reply to a PR review comment."""
    tagged_body = f"{body}\n\n{REPLY_SIGNATURE}"
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/comments/{comment_id}/replies"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "ai-pr-review-action",
    }
    payload = json.dumps({"body": tagged_body}).encode("utf-8")
    return safe_request(url, data=payload, headers=headers)


def find_existing_comment(owner: str, repo: str, pr_number: int, token: str) -> int | None:
    """Find existing review comment by signature. Returns comment ID or None."""
    url = f"https://api.github.com/repos/{owner}/{repo}/issues/{pr_number}/comments?per_page=100"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "ai-pr-review-action",
    }
    try:
        result = safe_request(url, headers=headers)
    except (urllib.error.HTTPError, urllib.error.URLError):
        return None
    for comment in result:
        if REVIEW_SIGNATURE in comment.get("body", ""):
            return comment["id"]
    return None


def update_comment(owner: str, repo: str, comment_id: int, token: str, body: str) -> dict:
    """Update an existing issue comment."""
    url = f"https://api.github.com/repos/{owner}/{repo}/issues/comments/{comment_id}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "ai-pr-review-action",
    }
    payload = json.dumps({"body": body}).encode("utf-8")
    result = safe_request(url, data=payload, headers=headers)
    print(f"Review comment updated: {result.get('html_url', 'ok')}")
    return result


def get_latest_commit(owner: str, repo: str, pr_number: int, token: str) -> str | None:
    """Get the latest commit SHA on the PR head."""
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "ai-pr-review-action",
    }
    try:
        result = safe_request(url, headers=headers)
        return result.get("head", {}).get("sha")
    except (urllib.error.HTTPError, urllib.error.URLError):
        return None


def find_existing_review(owner: str, repo: str, pr_number: int, token: str) -> int | None:
    """Find existing AI review by signature. Returns review ID or None."""
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/reviews?per_page=100"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "ai-pr-review-action",
    }
    try:
        result = safe_request(url, headers=headers)
    except (urllib.error.HTTPError, urllib.error.URLError):
        return None
    for review in result:
        if REVIEW_SIGNATURE in review.get("body", ""):
            return review["id"]
    return None


def has_bot_reviews(owner: str, repo: str, pr_number: int, token: str) -> bool:
    """Check if bot already posted inline comments (to prevent duplicates)."""
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/reviews?per_page=100"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "ai-pr-review-action",
    }
    try:
        result = safe_request(url, headers=headers)
    except (urllib.error.HTTPError, urllib.error.URLError):
        return False
    for review in result:
        user = review.get("user", {}).get("login", "")
        body = review.get("body", "")
        if user == "github-actions[bot]" and review.get("state") == "COMMENTED":
            if REVIEW_SIGNATURE in body or body == "":
                return True
    return False


def delete_review(owner: str, repo: str, pr_number: int, review_id: int, token: str) -> bool:
    """Delete an existing review. Returns True on success."""
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/reviews/{review_id}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "ai-pr-review-action",
    }
    try:
        req = urllib.request.Request(url, headers=headers, method="DELETE")
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as e:
        print(f"WARNING: Could not delete review {review_id}: {e.code} {e.reason}", file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# Review text processing
# ---------------------------------------------------------------------------

def strip_preamble(text: str) -> str:
    """Remove any text before the ## PR Review heading."""
    idx = text.find("## PR Review")
    if idx > 0:
        return text[idx:]
    return text


def extract_issues_json(review_text: str) -> tuple[str, list[dict] | None]:
    """Extract structured issues JSON from review text.

    Returns (clean_text, issues) where clean_text has the JSON block removed.
    """
    match = _JSON_BLOCK_PATTERN.search(review_text)
    if not match:
        return review_text, None
    json_str = match.group(1).strip()
    try:
        issues = json.loads(json_str)
        if not isinstance(issues, list):
            return review_text, None
        clean_text = review_text[: match.start()].rstrip()
        return clean_text, issues
    except (json.JSONDecodeError, ValueError) as e:
        print(f"WARNING: Failed to parse issues JSON: {e}", file=sys.stderr)
        return review_text, None


def extract_replies_json(review_text: str) -> tuple[str, list[dict] | None]:
    """Extract REVIEW_REPLIES_JSON block from review text.

    Returns (clean_text, replies) where clean_text has the JSON block removed.
    """
    match = _REPLIES_PATTERN.search(review_text)
    if not match:
        return review_text, None
    json_str = match.group(1).strip()
    try:
        replies = json.loads(json_str)
        if not isinstance(replies, list):
            return review_text, None
        clean_text = review_text[: match.start()].rstrip()
        return clean_text, replies
    except (json.JSONDecodeError, ValueError) as e:
        print(f"WARNING: Failed to parse replies JSON: {e}", file=sys.stderr)
        return review_text, None


def strip_key_issues(review_text: str) -> str:
    """Remove Key Issues and Code Improvements sections from summary comment."""
    text = re.sub(r"### Key Issues.*?(?=### |\Z)", "", review_text, flags=re.DOTALL)
    text = re.sub(r"### Code Improvements.*?(?=### |\Z)", "", text, flags=re.DOTALL)
    return text.strip()


# ---------------------------------------------------------------------------
# Inline review comments
# ---------------------------------------------------------------------------

def post_inline_comments(
    owner: str, repo: str, pr_number: int, token: str,
    issues: list[dict], commit_sha: str,
) -> bool:
    """Post inline review comments via PR Reviews API. Returns True on success."""
    if not issues or not commit_sha:
        return False

    # Check if bot already posted inline comments (prevent duplicates)
    if has_bot_reviews(owner, repo, pr_number, token):
        print("Bot already posted inline comments, skipping to prevent duplicates")
        return False

    # Delete existing review if updating
    review_id = find_existing_review(owner, repo, pr_number, token)
    if review_id:
        delete_review(owner, repo, pr_number, review_id, token)

    comments = []
    for issue in issues:
        file_path = issue.get("file_path", "")
        line = issue.get("line")
        if not file_path or not line:
            continue

        severity = issue.get("severity", "NOTE")
        title = issue.get("title", "Issue")
        body = issue.get("body", "")

        severity_label = {"CAUTION": "Critical", "WARNING": "Warning", "NOTE": "Suggestion"}.get(severity, "Note")
        severity_icon = {"CAUTION": "\U0001f534", "WARNING": "\U0001f7e1", "NOTE": "\U0001f535"}.get(severity, "\U0001f535")
        comment_body = f"{severity_icon} **[{severity_label}] {title}**\n\n{body}"

        comments.append({
            "path": file_path,
            "line": int(line),
            "side": "RIGHT",
            "body": comment_body,
        })

    if not comments:
        return False

    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/reviews"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "ai-pr-review-action",
    }
    payload = json.dumps({
        "body": "",
        "event": "COMMENT",
        "comments": comments,
        "commit_id": commit_sha,
    }).encode("utf-8")

    try:
        result = safe_request(url, data=payload, headers=headers)
        print(f"Inline review posted: {result.get('html_url', 'ok')}")
        return True
    except urllib.error.HTTPError as e:
        print(f"WARNING: Inline review failed ({e.code}). Comments may reference lines outside the diff.", file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# CLI entry point (for OpenCode engine integration)
# ---------------------------------------------------------------------------

def main_cli():
    """CLI entry point for fetching review context."""
    import argparse

    parser = argparse.ArgumentParser(description="Fetch review context for PR")
    parser.add_argument("--owner", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--token", default=None, help="GitHub token (falls back to GITHUB_TOKEN env var)")
    parser.add_argument("--mode", choices=["context", "replies"], default="context",
                        help="Output mode: context (for LLM prompt) or replies (user reply threads)")
    args = parser.parse_args()
    args.token = args.token or os.environ.get("GITHUB_TOKEN")
    if not args.token:
        print("Error: --token or GITHUB_TOKEN env var required", file=sys.stderr)
        sys.exit(1)

    try:
        threads = fetch_unresolved_threads(args.owner, args.repo, args.pr, args.token)
        threads = filter_threads(threads)
        if not threads:
            return

        if args.mode == "context":
            output = format_review_context(threads)
        else:
            replies = find_user_replies(threads)
            output = format_reply_context(replies)

        if output:
            print(output)
    except Exception as e:
        print(f"WARNING: Could not fetch review context: {e}", file=sys.stderr)


if __name__ == "__main__":
    main_cli()
