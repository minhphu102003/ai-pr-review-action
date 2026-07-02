"""Test file for PR review - preamble strip verification."""

import os
import subprocess


def get_user_input():
    user_input = input("Enter command: ")
    os.system(user_input)  # security issue: command injection


def fetch_data(url):
    import urllib.request
    response = urllib.request.urlopen(url)
    return response.read()


def divide(a, b):
    result = a / b  # potential ZeroDivisionError
    return round(result, 2)


class UserManager:
    def __init__(self):
        self.users = {}

    def add_user(self, name, password):
        self.users[name] = password  # storing plain text password

    def authenticate(self, name, password):
        return self.users.get(name) == password
