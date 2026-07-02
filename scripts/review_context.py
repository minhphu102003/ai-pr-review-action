#!/usr/bin/env python3
"""
Review context: fetch unresolved PR review threads via GraphQL,
detect user replies, format context for LLM, and post replies.
Stdlib only — no pip dependencies. Python 3.10+.
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

HTTP_TIMEOUT = int(os.environ.get("HTTP_TIMEOUT", "120"))
GRAPHQL_URL = "https://api.github.com/graphql"
REVIEW_SIGNATURE = "AI Review by ai-pr-review-action"
REPLY_SIGNATURE = "<!-- AI_REVIEW_REPLY -->"

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
    for _ in range(4):  # max 4 pages × 50 = 200 threads
        variables = {"owner": owner, "repo": repo, "pr": pr_number, "after": after}
        result = _graphql(token, _GRAPHQL_QUERY, variables)
        pr_data = result["data"]["repository"]["pullRequest"]
        thread_conn = pr_data["reviewThreads"]
        for node in thread_conn["nodes"]:
            # Filter resolved threads client-side
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


def filter_threads(threads: list[dict]) -> list[dict]:
    """Remove outdated threads and threads with no user replies."""
    filtered = []
    for t in threads:
        if t["is_outdated"]:
            continue
        # Check if there's at least one non-bot reply
        has_user_reply = any(
            not c["body"].startswith("[") and REVIEW_SIGNATURE not in c["body"]
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

        # First comment is the bot's inline comment
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


def format_reply_context(replies: list[dict], max_chars: int = 4000) -> str:
    """Format user replies as context for LLM to generate responses."""
    if not replies:
        return ""

    lines = [
        "<user_replies>",
        "The user replied to your previous review comments. You decide whether to reply:",
        "- If user says 'fixed' or 'done' → no reply needed, skip",
        "- If user is debating or asking questions → generate a reply",
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


def post_reply(owner: str, repo: str, pr_number: int, comment_id: int, body: str, token: str) -> dict:
    """Post a reply to a PR review comment."""
    # Tag bot replies so we don't process them as user replies later
    tagged_body = f"{body}\n\n{REPLY_SIGNATURE}"
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/comments/{comment_id}/replies"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "ai-pr-review-action",
    }
    payload = json.dumps({"body": tagged_body}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def extract_replies_json(review_text: str) -> tuple[str, list[dict] | None]:
    """Extract REVIEW_REPLIES_JSON block from review text.

    Returns (clean_text, replies) where clean_text has the JSON block removed.
    """
    pattern = re.compile(r"<!--\s*REVIEW_REPLIES_JSON\s*\n(.*?)\n\s*-->", re.DOTALL)
    match = pattern.search(review_text)
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


def main_cli():
    """CLI entry point for OpenCode engine integration."""
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
