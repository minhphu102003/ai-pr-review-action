#!/usr/bin/env python3
"""
Post inline review comments for OpenCode engine.
Finds the latest PR comment, extracts issues JSON, and posts inline comments.
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from review_context import (
    REVIEW_SIGNATURE, delete_issue_comment, extract_issues_json,
    extract_replies_json, fetch_unresolved_threads, filter_threads,
    find_user_replies, get_env, get_github_info, get_latest_commit,
    mask_secrets, post_inline_comments, post_reply, safe_request,
    strip_key_issues, strip_preamble, update_comment,
)


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
        if re.search(r"<!--\s*REVIEW_ISSUES_JSON\s*\n(.*?)\n\s*-->", body, re.DOTALL):
            return comment
    return None


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
        if re.search(r"<!--\s*REVIEW_REPLIES_JSON\s*\n(.*?)\n\s*-->", body, re.DOTALL):
            return comment
    return None


def main():
    mask_secrets()
    owner, repo, pr_number = get_github_info()
    token = get_env("GITHUB_TOKEN", required=True)
    skip_inline = get_env("SKIP_INLINE_COMMENTS").lower() in ("1", "true", "yes")

    # Find the latest comment with JSON block (either REVIEW_ISSUES_JSON or REVIEW_REPLIES_JSON)
    comment = find_latest_comment(owner, repo, pr_number, token)
    if not comment:
        comment = find_latest_comment_with_replies(owner, repo, pr_number, token)
        if not comment:
            print("No comment with REVIEW_ISSUES_JSON or REVIEW_REPLIES_JSON found. Skipping.")
            return

    comment_id = comment["id"]
    original_body = comment["body"]
    body = strip_preamble(original_body)
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

    # Extract issues and build summary text
    clean_text, issues = extract_issues_json(clean_text)

    if issues:
        summary_text = strip_key_issues(clean_text)
        print(f"Found {len(issues)} issues")
    else:
        summary_text = clean_text
        print("No valid issues found in JSON block.")

    # Re-review dedup: find existing summary comment (not the current one)
    existing_id = None
    try:
        url = f"https://api.github.com/repos/{owner}/{repo}/issues/{pr_number}/comments?per_page=100&sort=created&direction=desc"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "ai-pr-review-action",
        }
        for c in safe_request(url, headers=headers):
            if c["id"] != comment_id and REVIEW_SIGNATURE in c.get("body", ""):
                existing_id = c["id"]
                break
    except Exception as e:
        print(f"WARNING: Could not search for existing summary: {e}", file=sys.stderr)

    if existing_id:
        print(f"Re-review: updating existing comment {existing_id}")
        if summary_text != original_body:
            update_comment(owner, repo, existing_id, token, summary_text)
        else:
            print("Summary unchanged, skipping update")
        delete_issue_comment(owner, repo, comment_id, token)
    else:
        if summary_text != original_body:
            update_comment(owner, repo, comment_id, token, summary_text)
            print("Updated summary comment")
        else:
            print("Summary unchanged, skipping update")

    # Skip inline comments if OpenCode engine already posts them
    if skip_inline:
        print("Skipping inline comments (SKIP_INLINE_COMMENTS=true)")
        return

    if not issues:
        return

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
