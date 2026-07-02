#!/usr/bin/env python3
"""
Post inline review comments for OpenCode engine.
Finds the latest PR comment, extracts issues JSON, and posts inline comments.
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from review_context import (
    fetch_unresolved_threads, filter_threads, find_user_replies,
    post_reply, extract_replies_json,
)

HTTP_TIMEOUT = int(os.environ.get("HTTP_TIMEOUT", "120"))
REVIEW_SIGNATURE = "Synaptic PR Review"

_JSON_BLOCK_PATTERN = re.compile(
    r"<!--\s*REVIEW_ISSUES_JSON\s*\n(.*?)\n\s*-->",
    re.DOTALL,
)


def get_env(name: str, required: bool = False) -> str:
    val = os.environ.get(name, "").strip()
    if required and not val:
        print(f"Error: {name} environment variable not set", file=sys.stderr)
        sys.exit(1)
    return val


def safe_request(url: str, data: bytes | None = None, headers: dict | None = None, method: str | None = None) -> dict:
    if method is None:
        method = "POST" if data else "GET"
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def get_github_info() -> tuple[str, str, int]:
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


def find_latest_comment(owner: str, repo: str, pr_number: int, token: str) -> dict | None:
    """Find the latest PR comment that contains REVIEW_ISSUES_JSON."""
    url = f"https://api.github.com/repos/{owner}/{repo}/issues/{pr_number}/comments?per_page=30&sort=created&direction=desc"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "ai-pr-review-action",
    }
    result = safe_request(url, headers=headers)
    for comment in result:
        body = comment.get("body", "")
        if _JSON_BLOCK_PATTERN.search(body):
            return comment
    return None


_REPLIES_PATTERN = re.compile(
    r"<!--\s*REVIEW_REPLIES_JSON\s*\n(.*?)\n\s*-->",
    re.DOTALL,
)


def find_latest_comment_with_replies(owner: str, repo: str, pr_number: int, token: str) -> dict | None:
    """Find the latest PR comment that contains REVIEW_REPLIES_JSON."""
    url = f"https://api.github.com/repos/{owner}/{repo}/issues/{pr_number}/comments?per_page=30&sort=created&direction=desc"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "ai-pr-review-action",
    }
    result = safe_request(url, headers=headers)
    for comment in result:
        body = comment.get("body", "")
        if _REPLIES_PATTERN.search(body):
            return comment
    return None


def extract_issues_json(review_text: str) -> tuple[str, list[dict] | None]:
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


def strip_key_issues(review_text: str) -> str:
    text = re.sub(r"### Key Issues.*?(?=### |\Z)", "", review_text, flags=re.DOTALL)
    text = re.sub(r"### Code Improvements.*?(?=### |\Z)", "", text, flags=re.DOTALL)
    return text.strip()


def get_latest_commit(owner: str, repo: str, pr_number: int, token: str) -> str | None:
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "ai-pr-review-action",
    }
    result = safe_request(url, headers=headers)
    return result.get("head", {}).get("sha")


def find_existing_review(owner: str, repo: str, pr_number: int, token: str) -> int | None:
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/reviews?per_page=100"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "ai-pr-review-action",
    }
    result = safe_request(url, headers=headers)
    for review in result:
        # Look for reviews with our signature
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
    result = safe_request(url, headers=headers)
    for review in result:
        user = review.get("user", {}).get("login", "")
        body = review.get("body", "")
        if user == "github-actions[bot]" and review.get("state") == "COMMENTED":
            if REVIEW_SIGNATURE in body or body == "":
                return True
    return False


def delete_review(owner: str, repo: str, pr_number: int, review_id: int, token: str) -> bool:
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


def update_comment(owner: str, repo: str, comment_id: int, token: str, body: str):
    url = f"https://api.github.com/repos/{owner}/{repo}/issues/comments/{comment_id}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "ai-pr-review-action",
    }
    payload = json.dumps({"body": body}).encode("utf-8")
    safe_request(url, data=payload, headers=headers)


def post_inline_comments(
    owner: str, repo: str, pr_number: int, token: str,
    issues: list[dict], commit_sha: str,
) -> bool:
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
        severity_icon = {"CAUTION": "🔴", "WARNING": "🟡", "NOTE": "🔵"}.get(severity, "🔵")
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


def main():
    owner, repo, pr_number = get_github_info()
    token = get_env("GITHUB_TOKEN", required=True)
    skip_inline = get_env("SKIP_INLINE_COMMENTS").lower() in ("1", "true", "yes")

    # Find the latest comment with JSON block (either REVIEW_ISSUES_JSON or REVIEW_REPLIES_JSON)
    comment = find_latest_comment(owner, repo, pr_number, token)
    if not comment:
        # Also check for REVIEW_REPLIES_JSON if REVIEW_ISSUES_JSON not found
        comment = find_latest_comment_with_replies(owner, repo, pr_number, token)
        if not comment:
            print("No comment with REVIEW_ISSUES_JSON or REVIEW_REPLIES_JSON found. Skipping.")
            return

    comment_id = comment["id"]
    body = comment["body"]
    print(f"Found comment {comment_id}")

    # Extract and post replies to user comments (process first since it's more important)
    clean_text, reply_list = extract_replies_json(body)
    if reply_list:
        try:
            threads = fetch_unresolved_threads(owner, repo, pr_number, token)
            threads = filter_threads(threads)
            replies_needed = find_user_replies(threads)
            valid_ids = {r["comment_id"] for r in replies_needed}
            for reply in reply_list:
                cid = reply.get("comment_id")
                reply_body = reply.get("body", "")
                if cid in valid_ids and reply_body:
                    try:
                        post_reply(owner, repo, pr_number, cid, reply_body, token)
                        print(f"Replied to comment {cid}")
                    except Exception as e:
                        print(f"WARNING: Failed to reply to comment {cid}: {e}", file=sys.stderr)
        except Exception as e:
            print(f"WARNING: Could not process replies: {e}", file=sys.stderr)

    # Skip inline comments if OpenCode engine already posts them
    if skip_inline:
        print("Skipping inline comments (SKIP_INLINE_COMMENTS=true)")
        return

    # Extract and post inline comments (if REVIEW_ISSUES_JSON exists)
    clean_text, issues = extract_issues_json(clean_text)
    if not issues:
        print("No valid issues found in JSON block.")
        return

    print(f"Found {len(issues)} issues")

    # Update summary comment to strip Key Issues
    summary_text = strip_key_issues(clean_text)
    if summary_text != clean_text:
        update_comment(owner, repo, comment_id, token, summary_text)
        print(f"Updated summary comment (stripped Key Issues)")

    # Post inline comments
    commit_sha = get_latest_commit(owner, repo, pr_number, token)
    if commit_sha:
        success = post_inline_comments(owner, repo, pr_number, token, issues, commit_sha)
        if success:
            print("Inline comments posted successfully")
        else:
            print("Failed to post inline comments", file=sys.stderr)
    else:
        print("WARNING: Could not get commit SHA for inline review", file=sys.stderr)



if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
