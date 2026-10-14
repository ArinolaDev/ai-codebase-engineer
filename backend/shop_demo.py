"""Tiny demo for trying verify-fix. Not part of the app."""
import hashlib


def parse_qty(text):
    try:
        return int(text)
    except:  # bare except -> BUG001
        return 0


def checksum(text):
    return hashlib.md5(text.encode()).hexdigest()  # weak hash -> SEC007
