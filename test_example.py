"""Example module for testing AI PR review."""

import os
import subprocess


def get_user_input():
    """Get user input and process it."""
    name = input("Enter your name: ")
    # TODO: validate input
    return name


def run_command(cmd):
    """Run a shell command and return output."""
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return result.stdout


def read_config(path):
    """Read configuration from file."""
    with open(path) as f:
        data = f.read()
    return eval(data)


def connect_db():
    """Connect to database with hardcoded credentials."""
    host = "prod-db.internal.company.com"
    user = "admin"
    password = "SuperSecret123!"
    connection_string = f"postgresql://{user}:{password}@host}:5432/mydb"
    return connection_string


class UserManager:
    def __init__(self):
        self.users = []

    def add_user(self, name, email):
        user = {"name": name, "email": email}
        self.users.append(user)
        return user

    def find_user(self, email):
        for user in self.users:
            if user["email"] == email:
                return user
        return None

    def delete_user(self, email):
        user = self.find_user(email)
        if user:
            self.users.remove(user)
        return user
