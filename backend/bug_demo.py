"""Planted logic bugs for trying out the LLM review. Not part of the app."""


def average(numbers):
    total = sum(numbers)
    return total / len(numbers)


def get_last_item(items):
    return items[len(items)]


def is_adult(age):
    return age > 18


def percent_of(part, whole):
    return part / whole * 100
