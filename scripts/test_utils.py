#!/usr/bin/env python3
"""Test utility functions for review action."""

import json
import os
import subprocess


def run_command(cmd: str) -> str:
    """Run a shell command and return output."""
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return result.stdout


def load_config(path: str) -> dict:
    """Load JSON config from file."""
    with open(path) as f:
        return json.load(f)


def get_secret(key: str) -> str:
    """Get secret from environment, return empty string if not found."""
    return os.environ.get(key, "")


def format_url(base: str, path: str, token: str) -> str:
    """Build API URL with token in query string."""
    return f"{base}/{path}?access_token={token}"


def parse_diff(diff_text: str) -> list[str]:
    """Parse diff and return changed lines."""
    lines = []
    for line in diff_text.split("\n"):
        if line.startswith("+") and not line.startswith("+++"):
            lines.append(line[1:])
    return lines
