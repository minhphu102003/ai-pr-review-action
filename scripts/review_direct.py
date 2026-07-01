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
import time
import urllib.error
import urllib.request

HTTP_TIMEOUT = int(os.environ.get("HTTP_TIMEOUT", "120"))
REVIEW_SIGNATURE = "*AI Review by ai-pr-review-action*"

# Mask API keys in GitHub Actions logs to prevent accidental exposure.
for _key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GITHUB_TOKEN"):
    _val = os.environ.get(_key, "")
    if _val:
        print(f"::add-mask::{_val}")

# Known model prefixes for Anthropic (no public models API).
# OpenAI-compatible APIs are validated via GET /v1/models instead.
_ANTHROPIC_PREFIXES = ("claude-",)


def sanitize_review(text: str) -> str:
    """Sanitize LLM output before posting as GitHub comment."""
    import re
    # Strip @mentions to prevent unintended notifications
    text = re.sub(r'(?<!\w)@(\w+)', r'`\@\1`', text)
    # Strip markdown image tags with external URLs (tracking pixels)
    text = re.sub(r'!\[([^\]]*)\]\(https?://[^\)]+\)', r'[image: \1]', text)
    return text


def get_env(name: str, required: bool = False) -> str:
    val = os.environ.get(name, "").strip()
    if required and not val:
        print(f"Error: {name} is required but not set", file=sys.stderr)
        sys.exit(1)
    return val


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
            # Find closest match for suggestion
            suggestion = _closest_model(model, available)
            msg = f"Error: Model '{model}' not found."
            if suggestion:
                msg += f" Did you mean '{suggestion}'?"
            print(msg, file=sys.stderr)
            print(f"Available models: {', '.join(sorted(available)[:20])}{'...' if len(available) > 20 else ''}", file=sys.stderr)
            sys.exit(1)
        print(f"Model '{model}' verified.", file=sys.stderr)
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, TimeoutError) as e:
        # If models endpoint is unavailable, warn but don't block
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

    best, best_dist = None, len(target)  # threshold: at most len(target) edits
    for c in candidates:
        d = _edit_distance(target, c)
        if d < best_dist:
            best, best_dist = c, d
    return best


def safe_request(url: str, data: bytes | None = None, headers: dict | None = None, method: str | None = None, max_retries: int = 3, context: str = "api") -> dict:
    """HTTP request with error handling, timeout, and retry for transient errors."""
    if method is None:
        method = "POST" if data else "GET"
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)

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
            if e.code == 400:
                if context == "llm":
                    print("Hint: Bad request. Check that your model name is correct for the chosen provider.", file=sys.stderr)
                else:
                    print("Hint: Bad request. Check the request parameters.", file=sys.stderr)
            elif e.code == 401:
                print("Hint: Check your API key is correct and not expired.", file=sys.stderr)
            elif e.code == 403:
                print("Hint: Access denied. Check API key permissions.", file=sys.stderr)
            elif e.code == 404:
                if context == "llm":
                    print("Hint: Model or endpoint not found. Verify the model name and base URL.", file=sys.stderr)
                else:
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
    model_env = get_env("MODEL")

    if anthropic_key and openai_key:
        # Match provider to model name when both keys are set
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


def _list_dir_files(owner: str, repo: str, path: str, token: str, limit: int = 3, warn: bool = False) -> list[str]:
    """List files in a directory, sorted by most recently modified. Returns up to `limit` file paths."""
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "ai-pr-review-action",
    }
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            items = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError) as e:
        if warn:
            print(f"WARNING: Failed to list directory {path}: {e}", file=sys.stderr)
        return []

    if not isinstance(items, list):
        return []

    # Filter to files only (not subdirs), prefer .md files
    files = [i for i in items if i.get("type") == "file"]
    md_files = [i for i in files if i["name"].endswith((".md", ".txt", ".prompt"))]
    candidates = md_files if md_files else files

    # Sort by name descending (assumes date-prefixed names like 2026-01-spec.md)
    # If no date pattern, just take first N
    candidates.sort(key=lambda x: x["name"], reverse=True)
    return [c["path"] for c in candidates[:limit]]


def fetch_context_files(
    owner: str, repo: str, token: str, context_files_input: str | None
) -> str:
    """Fetch context files from repo for LLM context.

    Priority:
    1. User-specified files (context_files input, comma-separated)
    2. Auto-detect: CLAUDE.md, architecture docs, README

    If a path is a directory, fetches 1-3 most recent files from it.
    Logs warning for paths that don't exist.

    Returns concatenated file content, truncated to _CONTEXT_MAX_CHARS.
    Returns empty string on failure (non-blocking).
    """
    # Determine which files to fetch
    if context_files_input:
        raw_paths = [p.strip() for p in context_files_input.split(",") if p.strip()]
    else:
        raw_paths = list(_AUTO_CONTEXT_PATHS)

    # Resolve directories to individual files
    is_user_specified = bool(context_files_input)
    paths = []
    for p in raw_paths:
        # Try as file first
        content = _fetch_file_content(owner, repo, p, token, warn=is_user_specified)
        if content is not None:
            paths.append((p, content))
        else:
            # Try as directory
            dir_files = _list_dir_files(owner, repo, p, token, limit=3, warn=is_user_specified)
            if dir_files:
                for df in dir_files:
                    fc = _fetch_file_content(owner, repo, df, token, warn=is_user_specified)
                    if fc is not None:
                        paths.append((df, fc))

    parts = []
    total_chars = 0

    for path, content in paths:
        if total_chars >= _CONTEXT_MAX_CHARS:
            break

        # Truncate README to first 2000 chars
        if path.upper().endswith("README.md") and len(content) > 2000:
            content = content[:2000] + "\n... [truncated]"

        # Check budget
        remaining = _CONTEXT_MAX_CHARS - total_chars
        if len(content) > remaining:
            content = content[:remaining] + "\n... [truncated]"

        parts.append(f"--- {path} ---\n{content}")
        total_chars += len(content) + len(path) + 10  # header overhead

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


def _build_user_message(diff: str, context: str = "") -> str:
    """Build the user message with optional context files."""
    if context:
        return f"<context>\n{context}\n</context>\n\n<diff>\n```diff\n{diff}\n```\n</diff>"
    return f"Here is the PR diff to review:\n\n```diff\n{diff}\n```"


def call_openai(api_key: str, model: str, prompt: str, diff: str, context: str = "") -> str:
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
            {"role": "user", "content": _build_user_message(diff, context)},
        ],
        "max_tokens": 4096,
        "temperature": 0.3,
    }).encode("utf-8")

    result = safe_request(url, data=body, headers=headers, context="llm")
    if "error" in result:
        print(f"OpenAI API error: {result['error']}", file=sys.stderr)
        sys.exit(1)
    if "choices" not in result or not result["choices"]:
        print(f"Unexpected OpenAI response: {json.dumps(result)[:500]}", file=sys.stderr)
        sys.exit(1)
    return result["choices"][0]["message"]["content"]


def call_anthropic(api_key: str, model: str, prompt: str, diff: str, context: str = "") -> str:
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
            {"role": "user", "content": _build_user_message(diff, context)},
        ],
    }).encode("utf-8")

    result = safe_request(url, data=body, headers=headers, context="llm")
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
        "Authorization": f"Bearer {token}",
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
        "Authorization": f"Bearer {token}",
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
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "ai-pr-review-action",
    }
    payload = json.dumps({"body": body}).encode("utf-8")
    result = safe_request(url, data=payload, headers=headers)
    print(f"Review comment posted: {result.get('html_url', 'ok')} (id: {result.get('id', '?')})")


# --- Inline review comments ---

_JSON_BLOCK_PATTERN = re.compile(
    r"<!--\s*REVIEW_ISSUES_JSON\s*\n(.*?)\n\s*-->",
    re.DOTALL,
)


def extract_issues_json(review_text: str) -> tuple[str, list[dict] | None]:
    """Extract structured issues JSON from review text.

    Returns (clean_text, issues) where clean_text has the JSON block removed.
    If no JSON block found or parsing fails, returns (review_text, None).
    """
    match = _JSON_BLOCK_PATTERN.search(review_text)
    if not match:
        return review_text, None

    json_str = match.group(1).strip()
    try:
        issues = json.loads(json_str)
        if not isinstance(issues, list):
            return review_text, None
        # Remove the JSON block from the review text
        clean_text = review_text[: match.start()].rstrip()
        return clean_text, issues
    except (json.JSONDecodeError, ValueError) as e:
        print(f"WARNING: Failed to parse issues JSON: {e}", file=sys.stderr)
        return review_text, None


def strip_key_issues(review_text: str) -> str:
    """Remove Key Issues and Code Improvements sections from summary comment.

    Keeps: PR table, What This PR Does, Flow Overview, Summary.
    The detailed issues are now inline comments.
    """
    # Remove ### Key Issues section (up to next ### or end)
    text = re.sub(
        r"### Key Issues.*?(?=### |\Z)",
        "",
        review_text,
        flags=re.DOTALL,
    )
    # Remove ### Code Improvements section (including <details> blocks)
    text = re.sub(
        r"### Code Improvements.*?(?=### |\Z)",
        "",
        text,
        flags=re.DOTALL,
    )
    return text.strip()


def get_latest_commit(owner: str, repo: str, pr_number: int, token: str) -> str | None:
    """Get the latest commit SHA on the PR head."""
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "ai-pr-review-action",
    }
    result = safe_request(url, headers=headers)
    return result.get("head", {}).get("sha")


def find_existing_review(owner: str, repo: str, pr_number: int, token: str) -> int | None:
    """Find existing AI review by signature. Returns review ID or None."""
    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/reviews?per_page=100"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "ai-pr-review-action",
    }
    result = safe_request(url, headers=headers)
    for review in result:
        if REVIEW_SIGNATURE in review.get("body", ""):
            return review["id"]
    return None


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


def post_inline_comments(
    owner: str,
    repo: str,
    pr_number: int,
    token: str,
    issues: list[dict],
    commit_sha: str,
    summary_body: str,
    update_existing: bool = True,
) -> bool:
    """Post inline review comments via PR Reviews API.

    Falls back to single issue comment if review creation fails.
    Returns True if inline review was posted successfully.
    """
    if not issues or not commit_sha:
        return False

    # Delete existing review if updating
    if update_existing:
        review_id = find_existing_review(owner, repo, pr_number, token)
        if review_id:
            delete_review(owner, repo, pr_number, review_id, token)

    # Build review comments
    comments = []
    for issue in issues:
        file_path = issue.get("file_path", "")
        line = issue.get("line")
        if not file_path or not line:
            continue

        severity = issue.get("severity", "NOTE")
        title = issue.get("title", "Issue")
        body = issue.get("body", "")

        # Build comment body with severity level
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

    # Post review with inline comments
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
    }).encode("utf-8")

    try:
        result = safe_request(url, data=payload, headers=headers)
        print(f"Inline review posted: {result.get('html_url', 'ok')}")
        return True
    except urllib.error.HTTPError as e:
        print(f"WARNING: Inline review failed ({e.code}). Comments may reference lines outside the diff.", file=sys.stderr)
        return False


def main():
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
            diff_max = 70000  # Reserve budget for context

    # Truncate diff
    diff = truncate_diff(diff, max_chars=diff_max)

    # Call LLM
    print(f"Calling {provider} API...")
    if provider == "openai":
        review = call_openai(api_key, model, prompt, diff, context)
    else:
        review = call_anthropic(api_key, model, prompt, diff, context)

    print(f"Review length: {len(review)} chars")

    # Sanitize and extract issues
    review = sanitize_review(review)
    clean_text, issues = extract_issues_json(review)

    # Post summary comment (non-resolvable, full review)
    post_comment(owner, repo, pr_number, token, clean_text, update_existing=update_existing)

    # Post inline comments (resolvable)
    if issues:
        commit_sha = get_latest_commit(owner, repo, pr_number, token)
        if commit_sha:
            success = post_inline_comments(
                owner, repo, pr_number, token, issues, commit_sha,
                summary_body=clean_text, update_existing=update_existing,
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
