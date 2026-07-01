"""Test file for verifying auto-reply with databaseId fix."""

import os
import sqlite3
import subprocess


def get_user(user_id):
    """Fetch user from database."""
    conn = sqlite3.connect("app.db")
    query = f"SELECT * FROM users WHERE id = {user_id}"
    result = conn.execute(query).fetchone()
    conn.close()
    return result


def run_command(cmd):
    """Execute a shell command."""
    output = subprocess.check_output(cmd, shell=True)
    return output.decode("utf-8")


def read_file(path):
    """Read file contents."""
    with open(path) as f:
        return f.read()


def create_token():
    """Generate auth token."""
    return os.urandom(32).hex()


def divide(a, b):
    """Divide two numbers."""
    return a / b
