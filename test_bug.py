#!/usr/bin/env python3
"""Test file with intentional bugs for re-review dedup test."""


def add(a, b):
    return a - b  # BUG: should be a + b


def is_even(n):
    if n % 2 == 1:
        return True  # BUG: should return False for odd
    return True  # BUG: should return True for even, but this is also True for odd


def divide(a, b):
    if b == 0:
        return None  # BUG: division by zero — should raise, not return None
    return a / b


# BUG: unused function
def unused_helper(x):
    return x * 2


class Calculator:
    """Calculator with intentional bugs."""

    def multiply(self, a, b):
        return a + b  # BUG: should be a * b

    def subtract(self, a, b):
        return b - a  # BUG: should be a - b
