#!/usr/bin/env python3
"""Local account administration for CHAKRA-AI.

Accounts are intentionally provisioned out-of-band so users cannot self-select
privileged roles in the browser.
"""
from __future__ import annotations
import argparse
import getpass
import os
import re
import sqlite3
import sys
import time
from pathlib import Path
from argon2 import PasswordHasher

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("CHAKRA_DB_PATH", str(ROOT / "yugam" / "data" / "chakra_security.db")))
ROLES = ["Production Manager", "Sustainability Officer", "Compliance Auditor", "Security Admin"]
EMAIL_RE = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,63}$")
PH = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4, hash_len=32, salt_len=16)


def db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn=sqlite3.connect(DB_PATH)
    conn.row_factory=sqlite3.Row
    return conn


def ensure_schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT NOT NULL UNIQUE COLLATE NOCASE,
        name TEXT NOT NULL,
        factory TEXT NOT NULL,
        role TEXT NOT NULL,
        password_hash TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 1,
        failed_attempts INTEGER NOT NULL DEFAULT 0,
        locked_until INTEGER NOT NULL DEFAULT 0,
        created_at INTEGER NOT NULL
    )""")


def validate_password(password: str):
    if len(password) < 12:
        raise ValueError("Password must be at least 12 characters.")
    if len(password) > 128:
        raise ValueError("Password must be at most 128 characters.")


def cmd_add(args):
    email=args.email.strip().lower()
    if not EMAIL_RE.fullmatch(email): raise SystemExit("Invalid email address.")
    if args.role not in ROLES: raise SystemExit("Invalid role.")
    name=args.name.strip(); factory=args.factory.strip()
    if len(name)<2 or len(name)>80: raise SystemExit("Name must be 2-80 characters.")
    if len(factory)<2 or len(factory)>120: raise SystemExit("Factory must be 2-120 characters.")
    password=getpass.getpass("New password (12+ chars): ")
    confirm=getpass.getpass("Confirm password: ")
    if password!=confirm: raise SystemExit("Passwords do not match.")
    validate_password(password)
    with db() as conn:
        ensure_schema(conn)
        try:
            conn.execute("INSERT INTO users(email,name,factory,role,password_hash,active,created_at) VALUES(?,?,?,?,?,1,?)",
                         (email,name,factory,args.role,PH.hash(password),int(time.time())))
        except sqlite3.IntegrityError:
            raise SystemExit("An account with that email already exists.")
    print(f"Created {email} as {args.role} for {factory}.")


def cmd_list(args):
    with db() as conn:
        ensure_schema(conn)
        rows=conn.execute("SELECT email,name,factory,role,active,locked_until FROM users ORDER BY factory,role,email").fetchall()
    if not rows:
        print("No users."); return
    for r in rows:
        state="active" if r["active"] else "disabled"
        if r["locked_until"] and r["locked_until"]>int(time.time()): state="locked"
        print(f'{r["email"]:<34} {r["role"]:<23} {state:<8} {r["factory"]}')


def find_user(conn,email):
    return conn.execute("SELECT * FROM users WHERE email=? COLLATE NOCASE",(email.strip().lower(),)).fetchone()


def cmd_reset(args):
    password=getpass.getpass("New password (12+ chars): "); confirm=getpass.getpass("Confirm password: ")
    if password!=confirm: raise SystemExit("Passwords do not match.")
    validate_password(password)
    with db() as conn:
        ensure_schema(conn); u=find_user(conn,args.email)
        if not u: raise SystemExit("User not found.")
        conn.execute("UPDATE users SET password_hash=?,failed_attempts=0,locked_until=0 WHERE id=?",(PH.hash(password),u["id"]))
    print("Password reset and lockout cleared.")


def cmd_state(args,active):
    with db() as conn:
        ensure_schema(conn); u=find_user(conn,args.email)
        if not u: raise SystemExit("User not found.")
        conn.execute("UPDATE users SET active=?,failed_attempts=0,locked_until=0 WHERE id=?",(1 if active else 0,u["id"]))
    print("Account enabled." if active else "Account disabled and access denied on next request.")


def main():
    parser=argparse.ArgumentParser(description="Manage CHAKRA-AI local accounts")
    sub=parser.add_subparsers(dest="cmd",required=True)
    a=sub.add_parser("add"); a.add_argument("--email",required=True); a.add_argument("--name",required=True); a.add_argument("--factory",required=True); a.add_argument("--role",required=True,choices=ROLES); a.set_defaults(fn=cmd_add)
    l=sub.add_parser("list"); l.set_defaults(fn=cmd_list)
    r=sub.add_parser("reset-password"); r.add_argument("--email",required=True); r.set_defaults(fn=cmd_reset)
    d=sub.add_parser("disable"); d.add_argument("--email",required=True); d.set_defaults(fn=lambda x:cmd_state(x,False))
    e=sub.add_parser("enable"); e.add_argument("--email",required=True); e.set_defaults(fn=lambda x:cmd_state(x,True))
    args=parser.parse_args(); args.fn(args)

if __name__=="__main__": main()
