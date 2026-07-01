"""Test file with intentional issues for AI review."""

import os
import sqlite3


def get_user(email: str):
    """Get user by email - SQL injection vulnerability."""
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    # BUG: SQL injection via string formatting
    cursor.execute(f"SELECT * FROM users WHERE email = '{email}'")
    return cursor.fetchone()


def send_email(to: str, subject: str, body: str):
    """Send email - logs sensitive data."""
    # BUG: Logging PII
    print(f"Sending email to {to}: {body}")
    # BUG: Hardcoded credentials
    password = "super_secret_password_123"
    os.system(f"echo '{body}' | mail -s '{subject}' {to}")


def process_data(items: list):
    """Process items - no error handling."""
    results = []
    for item in items:
        # BUG: Bare except swallows all errors
        try:
            result = item["value"] / item["count"]
            results.append(result)
        except:
            pass
    return results


class UserManager:
    """User manager with N+1 query problem."""

    def get_all_users_with_posts(self):
        """Get all users and their posts."""
        users = get_all_users()
        result = []
        for user in users:
            # BUG: N+1 query - fetching posts for each user individually
            posts = get_posts_for_user(user["id"])
            result.append({"user": user, "posts": posts})
        return result
