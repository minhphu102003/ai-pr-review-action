"""Test file with intentional issues for PR review testing."""

import os
import subprocess
import sqlite3


# SQL Injection vulnerability
def get_user(username):
    conn = sqlite3.connect("app.db")
    query = f"SELECT * FROM users WHERE username = '{username}'"
    cursor = conn.execute(query)
    return cursor.fetchone()


# Hardcoded credentials
DATABASE_PASSWORD = "super_secret_password_123"
API_KEY = "sk-1234567890abcdef"


# Command injection
def run_command(user_input):
    result = subprocess.run(f"echo {user_input}", shell=True, capture_output=True)
    return result.stdout


# Bare except
def risky_operation():
    try:
        return 1 / 0
    except:
        return None


# N+1 query pattern
def get_all_users_with_posts():
    conn = sqlite3.connect("app.db")
    users = conn.execute("SELECT * FROM users").fetchall()
    result = []
    for user in users:
        posts = conn.execute(
            f"SELECT * FROM posts WHERE user_id = {user[0]}"
        ).fetchall()
        result.append({"user": user, "posts": posts})
    return result
