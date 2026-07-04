#!/usr/bin/env python3
"""Test file with intentional bugs for re-review dedup test."""


def add(a, b):
    return a + b


def is_even(n):
    return n % 2 == 0


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
