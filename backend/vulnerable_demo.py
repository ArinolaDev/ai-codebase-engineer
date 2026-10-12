"""
Deliberately insecure demo file for testing the scanner. DO NOT use in real code.
Zip this file and upload it via POST /api/projects/upload, then scan it.
"""
import hashlib
import os
import sqlite3

API_KEY = "sk-demo1234567890abcdefghijklmnop"
db_password = "supersecret123"


def get_user(user_id):
    conn = sqlite3.connect("app.db")
    cur = conn.cursor()
    cur.execute(f"SELECT * FROM users WHERE id = {user_id}")  # SQL injection
    return cur.fetchone()


def hash_password(pw, history=[]):  # mutable default
    history.append(pw)
    return hashlib.md5(pw.encode()).hexdigest()  # weak hash


def run_command(cmd):
    os.system(cmd)  # command injection
    try:
        return eval(cmd)  # arbitrary code execution
    except:  # bare except
        return None
