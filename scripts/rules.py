#!/usr/bin/env python3
"""
Repository Memory Rules for AI PR Review action.
Manages .synaptic/rules.json — load, save, validate, and format rules.
Handles @synaptic-ai remember commands from PR comments.
Stdlib only — no pip dependencies. Python 3.10+.
"""

import base64
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from review_context import HTTP_TIMEOUT, get_env, safe_request

RULES_PATH = ".synaptic/rules.json"
MAX_RULE_LENGTH = 500
MAX_RULES = 50
MAX_TOTAL_CHARS = 5000
_REMEMBER_PATTERN = re.compile(r"@synaptic-ai\s+remember:\s*(.+?)(?:\n|$)", re.IGNORECASE)
_EXTRACT_PROMPT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "prompts", "extract_rule.txt")
_REMEMBER_JSON_PATTERN = re.compile(
    r"<!--\s*REMEMBER_RULE_JSON\s*\n(.*?)\n\s*-->",
    re.DOTALL,
)

# Default models — same as review_direct.py engine defaults
_DEFAULT_OPENAI_MODEL = "gpt-4.1-mini"
_DEFAULT_ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"


# ---------------------------------------------------------------------------
# LLM extraction — reuses the same engine (OpenAI/Anthropic) as the review
# ---------------------------------------------------------------------------

def _build_api_url(base_url_env: str, default_url: str, path: str) -> str:
    """Build API URL from base_url, avoiding double /v1."""
    base_url = (os.environ.get(base_url_env, "").strip() or default_url).rstrip("/")
    if base_url.endswith("/v1"):
        base_url = base_url[:-3]
    return f"{base_url}/v1/{path}"


def _load_extract_prompt() -> str:
    """Load the rule extraction prompt from file."""
    try:
        with open(_EXTRACT_PROMPT_PATH, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return (
            "Extract the coding convention or review preference from the user's message. "
            "Return ONLY the rule text as a single clear statement. "
            "If no rule is found, return exactly: NO_RULE"
        )


def _detect_llm() -> tuple[str, str, str] | None:
    """Detect available LLM from env vars. Returns (provider, api_key, model) or None.

    OpenCode users: OPENCODE_API_KEY cannot be used for direct HTTP calls.
    Add OPENAI_API_KEY or ANTHROPIC_API_KEY as a secret to enable LLM extraction.
    """
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    opencode_key = os.environ.get("OPENCODE_API_KEY", "").strip()
    model_env = os.environ.get("MODEL", "").strip()

    if openai_key:
        model = model_env or _DEFAULT_OPENAI_MODEL
        return "openai", openai_key, model
    elif anthropic_key:
        model = model_env or _DEFAULT_ANTHROPIC_MODEL
        return "anthropic", anthropic_key, model
    elif opencode_key:
        print("OpenCode key detected but cannot be used for direct LLM calls. "
              "Add OPENAI_API_KEY or ANTHROPIC_API_KEY for smarter rule extraction.", file=sys.stderr)
    return None


def _call_llm(provider: str, api_key: str, model: str, system_prompt: str, user_message: str) -> str | None:
    """Call the LLM (OpenAI or Anthropic) and return the response text."""
    if provider == "openai":
        url = _build_api_url("OPENAI_BASE_URL", "https://api.openai.com", "chat/completions")
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        body = json.dumps({
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "max_tokens": 200,
            "temperature": 0.1,
        }).encode("utf-8")
    else:
        url = _build_api_url("ANTHROPIC_BASE_URL", "https://api.anthropic.com", "messages")
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        body = json.dumps({
            "model": model,
            "max_tokens": 200,
            "temperature": 0.1,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_message}],
        }).encode("utf-8")

    try:
        result = safe_request(url, data=body, headers=headers)
        if "error" in result:
            print(f"LLM error: {result['error']}", file=sys.stderr)
            return None

        if provider == "openai":
            return result.get("choices", [{}])[0].get("message", {}).get("content", "").strip() or None
        else:
            for block in result.get("content", []):
                if block.get("type") == "text":
                    return block["text"].strip() or None
            return None
    except Exception as e:
        print(f"LLM extraction failed: {e}", file=sys.stderr)
        return None


def extract_rule(comment_body: str) -> str | None:
    """Extract rule from comment using the same LLM engine as the review.

    Tries LLM first (using whatever engine is configured), falls back to regex.
    """
    llm = _detect_llm()
    if llm:
        provider, api_key, model = llm
        prompt = _load_extract_prompt()
        print(f"Extracting rule via {provider} ({model})...", file=sys.stderr)
        result = _call_llm(provider, api_key, model, prompt, comment_body)
        if result and result != "NO_RULE":
            print(f"LLM extracted rule: {result}", file=sys.stderr)
            return result
        print("LLM returned NO_RULE or empty, falling back to regex", file=sys.stderr)
    else:
        print("No LLM API key available, using regex fallback", file=sys.stderr)

    # Fallback: regex extraction
    regex_result = parse_remember_command(comment_body)
    if regex_result:
        print(f"Regex extracted rule: {regex_result}", file=sys.stderr)
    return regex_result


# ---------------------------------------------------------------------------
# GitHub API helpers
# ---------------------------------------------------------------------------

def get_file_sha(owner: str, repo: str, path: str, token: str) -> str | None:
    """Get the SHA of a file in the repo. Returns None if file doesn't exist."""
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "ai-pr-review-action",
    }
    try:
        result = safe_request(url, headers=headers)
        return result.get("sha")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def is_collaborator(owner: str, repo: str, username: str, token: str) -> bool:
    """Check if a user is a repo collaborator."""
    url = f"https://api.github.com/repos/{owner}/{repo}/collaborators/{username}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "ai-pr-review-action",
    }
    try:
        req = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return resp.status == 204
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        # For other errors (403, 5xx), fail open
        print(f"WARNING: Collaborator check failed ({e.code}), assuming collaborator", file=sys.stderr)
        return True
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"WARNING: Collaborator check error: {e}, assuming collaborator", file=sys.stderr)
        return True


def react_to_comment(owner: str, repo: str, comment_id: int, token: str, reaction: str = "+1") -> bool:
    """React to a PR comment."""
    url = f"https://api.github.com/repos/{owner}/{repo}/issues/comments/{comment_id}/reactions"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "ai-pr-review-action",
    }
    payload = json.dumps({"content": reaction}).encode("utf-8")
    try:
        safe_request(url, data=payload, headers=headers)
        return True
    except (urllib.error.HTTPError, urllib.error.URLError) as e:
        print(f"WARNING: Could not react to comment: {e}", file=sys.stderr)
        return False


def post_pr_comment(owner: str, repo: str, pr_number: int, token: str, body: str) -> None:
    """Post a comment on a PR issue."""
    url = f"https://api.github.com/repos/{owner}/{repo}/issues/{pr_number}/comments"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "ai-pr-review-action",
    }
    payload = json.dumps({"body": body}).encode("utf-8")
    try:
        result = safe_request(url, data=payload, headers=headers)
        print(f"Comment posted: {result.get('html_url', 'ok')}")
    except (urllib.error.HTTPError, urllib.error.URLError) as e:
        print(f"WARNING: Could not post comment: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Rules storage
# ---------------------------------------------------------------------------

def load_rules(owner: str, repo: str, token: str) -> tuple[list[dict], str | None]:
    """Load rules from .synaptic/rules.json. Returns (rules, sha)."""
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{RULES_PATH}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "ai-pr-review-action",
    }
    try:
        result = safe_request(url, headers=headers)
        sha = result.get("sha")
        content = result.get("content", "")
        decoded = base64.b64decode(content).decode("utf-8")
        data = json.loads(decoded)
        rules = data.get("rules", [])
        if not isinstance(rules, list):
            return [], sha
        return rules, sha
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return [], None
        print(f"WARNING: Failed to load rules: HTTP {e.code}", file=sys.stderr)
        return [], None
    except (json.JSONDecodeError, ValueError) as e:
        print(f"WARNING: Failed to parse rules.json: {e}", file=sys.stderr)
        return [], get_file_sha(owner, repo, RULES_PATH, token)


def save_rules(owner: str, repo: str, token: str, rules: list[dict], sha: str | None, branch: str | None = None) -> bool:
    """Commit updated rules.json to the repo. Retries once on SHA conflict."""
    data = {"version": 1, "rules": rules}
    content = json.dumps(data, indent=2, ensure_ascii=False)

    for attempt in range(2):
        payload = {
            "message": "chore: update repository memory rules",
            "content": base64.b64encode(content.encode("utf-8")).decode("utf-8"),
        }
        if sha:
            payload["sha"] = sha
        if branch:
            payload["branch"] = branch

        url = f"https://api.github.com/repos/{owner}/{repo}/contents/{RULES_PATH}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "ai-pr-review-action",
        }
        body = json.dumps(payload).encode("utf-8")
        try:
            result = safe_request(url, data=body, headers=headers)
            print(f"Rules committed: {result.get('content', {}).get('html_url', 'ok')}")
            return True
        except urllib.error.HTTPError as e:
            if e.code == 409 and attempt == 0:
                # SHA conflict — retry with fresh SHA
                print("SHA conflict, retrying with fresh SHA...", file=sys.stderr)
                new_sha = get_file_sha(owner, repo, RULES_PATH, token)
                if new_sha:
                    sha = new_sha
                    continue
            if e.code == 422:
                print("Branch protection may prevent direct commit.", file=sys.stderr)
            print(f"WARNING: Failed to save rules: HTTP {e.code}", file=sys.stderr)
            return False
        except (urllib.error.URLError, TimeoutError) as e:
            print(f"WARNING: Failed to save rules: {e}", file=sys.stderr)
            return False
    return False


# ---------------------------------------------------------------------------
# Command parsing and validation
# ---------------------------------------------------------------------------

def parse_remember_command(comment_body: str) -> str | None:
    """Extract rule text from @synaptic-ai remember: command."""
    match = _REMEMBER_PATTERN.search(comment_body)
    if not match:
        return None
    return match.group(1).strip()


def validate_rule(rule_text: str, existing: list[dict]) -> tuple[bool, str]:
    """Validate a rule. Returns (valid, error_message)."""
    if not rule_text:
        return False, "Rule text is empty."
    if len(rule_text) > MAX_RULE_LENGTH:
        return False, f"Rule is too long ({len(rule_text)} chars). Maximum is {MAX_RULE_LENGTH} characters."
    if len(existing) >= MAX_RULES:
        return False, f"Too many rules ({len(existing)}). Maximum is {MAX_RULES}. Please review and prune old rules."
    if deduplicate(rule_text, existing):
        return False, "This rule already exists."
    return True, ""


def deduplicate(new_rule: str, existing: list[dict]) -> bool:
    """Check if a rule already exists (case-insensitive exact match)."""
    new_lower = new_rule.lower().strip()
    return any(r.get("rule", "").lower().strip() == new_lower for r in existing)


def generate_rule_id(existing: list[dict]) -> str:
    """Generate the next sequential rule ID."""
    max_num = 0
    for r in existing:
        rid = r.get("id", "")
        if rid.startswith("rule-"):
            try:
                num = int(rid[5:])
                max_num = max(max_num, num)
            except ValueError:
                pass
    return f"rule-{max_num + 1:03d}"


# ---------------------------------------------------------------------------
# Prompt formatting
# ---------------------------------------------------------------------------

def format_rules_for_prompt(rules: list[dict]) -> str:
    """Format rules as an XML block for LLM prompt injection."""
    if not rules:
        return ""

    lines = [
        "<repository_rules>",
        "The repository has the following coding conventions. Enforce these during review.",
        "If the PR violates any rule, flag it as a Warning or Critical severity issue.",
        "",
    ]
    for i, r in enumerate(rules, 1):
        lines.append(f"{i}. {r.get('rule', '')}")
    lines.append("</repository_rules>")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Process remember from LLM output (OpenCode engine)
# ---------------------------------------------------------------------------

def extract_remember_json(review_text: str) -> str | None:
    """Extract rule from REMEMBER_RULE_JSON block in LLM output."""
    match = _REMEMBER_JSON_PATTERN.search(review_text)
    if not match:
        return None
    json_str = match.group(1).strip()
    try:
        data = json.loads(json_str)
        if isinstance(data, dict) and "rule" in data:
            rule = data["rule"].strip()
            return rule if rule else None
    except (json.JSONDecodeError, ValueError):
        pass
    return None


def find_latest_comment_with_remember(owner: str, repo: str, pr_number: int, token: str) -> str | None:
    """Find the latest bot comment containing REMEMBER_RULE_JSON.

    Only matches comments authored by github-actions[bot] to avoid picking up user comments.
    The REVIEW_SIGNATURE check is omitted because the extract prompt produces a minimal
    output (just the JSON block) without the full review signature.
    """
    url = f"https://api.github.com/repos/{owner}/{repo}/issues/{pr_number}/comments?per_page=100&sort=created&direction=desc"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "ai-pr-review-action",
    }
    try:
        comments = safe_request(url, headers=headers)
    except (urllib.error.HTTPError, urllib.error.URLError):
        return None

    for comment in comments:
        author = comment.get("user", {}).get("login", "")
        body = comment.get("body", "")
        if author == "github-actions[bot]" and "REMEMBER_RULE_JSON" in body:
            return body
    return None


def process_remember_from_comment(owner: str, repo: str, pr_number: int, token: str, comment_author: str) -> bool:
    """Extract rule from LLM output comment and save it. Returns True if rule was added."""
    # Collaborator check — only repo collaborators can add rules
    if not is_collaborator(owner, repo, comment_author, token):
        msg = f"Only repository collaborators can add memory rules. @{comment_author} does not have collaborator access."
        print(msg, file=sys.stderr)
        post_pr_comment(owner, repo, pr_number, token, f"> [!WARNING]\n> {msg}")
        return False

    body = find_latest_comment_with_remember(owner, repo, pr_number, token)
    if not body:
        return False

    rule_text = extract_remember_json(body)
    if not rule_text:
        return False

    # Load existing rules
    rules, sha = load_rules(owner, repo, token)

    # Validate
    valid, error = validate_rule(rule_text, rules)
    if not valid:
        print(f"Extracted rule validation failed: {error}", file=sys.stderr)
        return False

    # Add the rule
    new_rule = {
        "id": generate_rule_id(rules),
        "rule": rule_text,
        "createdBy": comment_author,
        "createdAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    rules.append(new_rule)

    if save_rules(owner, repo, token, rules, sha):
        print(f"Rule extracted and saved: {new_rule['id']} — {rule_text}")
        return True
    return False


# ---------------------------------------------------------------------------
# CLI entry points
# ---------------------------------------------------------------------------

def main_load():
    """CLI: Load rules and output formatted prompt block. Used by action.yml resolve-rules step."""
    import argparse

    parser = argparse.ArgumentParser(description="Load repository rules")
    parser.add_argument("--owner", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--token", default=None)
    args = parser.parse_args()
    token = args.token or os.environ.get("GITHUB_TOKEN")
    if not token:
        print("Error: --token or GITHUB_TOKEN env var required", file=sys.stderr)
        sys.exit(1)

    try:
        rules, _ = load_rules(args.owner, args.repo, token)
        output = format_rules_for_prompt(rules)
        if output:
            print(output)
    except Exception as e:
        print(f"WARNING: Could not load rules: {e}", file=sys.stderr)


def main_remember():
    """CLI: Process @synaptic-ai remember command. Used by action.yml remember step."""
    comment_body = get_env("COMMENT_BODY")
    comment_author = get_env("COMMENT_AUTHOR")
    comment_id_str = get_env("COMMENT_ID")
    repo = get_env("REPO", required=True)
    token = get_env("GITHUB_TOKEN", required=True)
    event_path = get_env("GITHUB_EVENT_PATH")

    if not comment_body or not comment_author:
        print("Missing COMMENT_BODY or COMMENT_AUTHOR", file=sys.stderr)
        sys.exit(1)

    owner, repo_name = repo.split("/", 1)

    # Get PR number early so we can post error comments
    pr_number = None
    if event_path:
        try:
            with open(event_path, encoding="utf-8") as f:
                event = json.load(f)
            pr_number = event.get("issue", {}).get("number")
        except (OSError, json.JSONDecodeError):
            pass

    # Collaborator check BEFORE extraction to avoid wasting LLM calls
    if not is_collaborator(owner, repo_name, comment_author, token):
        msg = f"Only repository collaborators can add memory rules. @{comment_author} does not have collaborator access."
        print(msg, file=sys.stderr)
        if pr_number:
            post_pr_comment(owner, repo_name, pr_number, token, f"> [!WARNING]\n> {msg}")
        sys.exit(0)

    # Extract the rule (LLM first, regex fallback)
    rule_text = extract_rule(comment_body)
    if not rule_text:
        print("No rule could be extracted from comment", file=sys.stderr)
        if pr_number:
            post_pr_comment(owner, repo_name, pr_number, token,
                            "> [!NOTE]\n> Could not extract a clear rule from your message. Try: `@synaptic-ai remember: <your rule>`")
        sys.exit(0)

    # Load existing rules
    rules, sha = load_rules(owner, repo_name, token)

    # Validate
    valid, error = validate_rule(rule_text, rules)
    if not valid:
        print(f"Validation failed: {error}", file=sys.stderr)
        if pr_number:
            post_pr_comment(owner, repo_name, pr_number, token, f"> [!WARNING]\n> Could not add rule: {error}")
        sys.exit(0)

    # Add the rule
    new_rule = {
        "id": generate_rule_id(rules),
        "rule": rule_text,
        "createdBy": comment_author,
        "createdAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    rules.append(new_rule)

    # Save
    if save_rules(owner, repo_name, token, rules, sha):
        print(f"Rule added: {new_rule['id']} — {rule_text}")
        # React to confirm
        if comment_id_str:
            try:
                react_to_comment(owner, repo_name, int(comment_id_str), token)
            except ValueError:
                pass
    else:
        # Fallback: show the rule JSON for manual addition
        print("Failed to save rules. Showing JSON for manual addition.", file=sys.stderr)
        if pr_number:
            fallback = json.dumps(new_rule, indent=2)
            post_pr_comment(
                owner, repo_name, pr_number, token,
                f"> [!WARNING]\n> Could not commit rule automatically. Add this to `.synaptic/rules.json`:\n\n```json\n{fallback}\n```",
            )


def main_process_remember():
    """CLI: Extract rule from LLM output comment. Used after OpenCode engine review."""
    repo = get_env("REPO", required=True)
    token = get_env("GITHUB_TOKEN", required=True)
    event_path = get_env("GITHUB_EVENT_PATH")

    owner, repo_name = repo.split("/", 1)

    # Get PR number and comment author
    pr_number = None
    comment_author = "unknown"
    if event_path:
        try:
            with open(event_path, encoding="utf-8") as f:
                event = json.load(f)
            pr_number = event.get("issue", {}).get("number") or event.get("pull_request", {}).get("number")
            comment_author = event.get("comment", {}).get("user", {}).get("login",
                           event.get("sender", {}).get("login", "unknown"))
        except (OSError, json.JSONDecodeError):
            pass

    if not pr_number:
        print("Could not determine PR number", file=sys.stderr)
        sys.exit(1)

    try:
        added = process_remember_from_comment(owner, repo_name, pr_number, token, comment_author)
        if not added:
            print("No rule found in LLM output", file=sys.stderr)
    except Exception as e:
        print(f"WARNING: Could not process remember: {e}", file=sys.stderr)


def main():
    """CLI dispatcher."""
    if len(sys.argv) < 2:
        print("Usage: rules.py {load|remember|process-remember}", file=sys.stderr)
        sys.exit(1)

    mode = sys.argv[1]
    if mode == "load":
        main_load()
    elif mode == "remember":
        main_remember()
    elif mode == "process-remember":
        main_process_remember()
    else:
        print(f"Unknown mode: {mode}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
