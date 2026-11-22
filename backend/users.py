"""Manage ABHEDYA accounts from the command line (run on the laptop, inside .venv).

    python -m backend.users list
    python -m backend.users add neha --role observer        # asks for the password twice
    python -m backend.users passwd commander                 # change a password (signs that user out everywhere)
    python -m backend.users remove neha

Roles: commander (may control missions and jammers) or observer (watch only).
"""
from __future__ import annotations

import argparse
import getpass
import sys
import time

from .auth import MIN_PASSWORD, ROLES, AuthStore
from .config import Settings


def _ask(prompt: str) -> str:
    a = getpass.getpass(prompt)
    if a != getpass.getpass("Type it again: "):
        sys.exit("The two passwords differ; nothing changed.")
    if len(a) < MIN_PASSWORD:
        sys.exit(f"Password must be at least {MIN_PASSWORD} characters; nothing changed.")
    return a


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="python -m backend.users", description="Manage ABHEDYA accounts.")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    a = sub.add_parser("add")
    a.add_argument("name")
    a.add_argument("--role", choices=ROLES, default="observer")
    pw = sub.add_parser("passwd")
    pw.add_argument("name")
    rm = sub.add_parser("remove")
    rm.add_argument("name")
    args = p.parse_args(argv)
    store = AuthStore(Settings().users_file)

    if args.cmd == "list":
        if not store.users:
            print("No accounts yet. Start the server once and it creates 'commander' and 'judge'.")
        for u in store.users.values():
            print(f"{u['name']:<20} {u['role']:<10} created {time.strftime('%Y-%m-%d', time.localtime(u['created']))}")
    elif args.cmd == "add":
        if args.name.lower() in store.users:
            sys.exit(f"'{args.name}' already exists (use passwd to change the password).")
        store.add_user(args.name, _ask(f"Password for {args.name}: "), args.role)
        print(f"Added {args.name} ({args.role}).")
    elif args.cmd == "passwd":
        rec = store.users.get(args.name.lower())
        if not rec:
            sys.exit(f"No user '{args.name}'.")
        store.add_user(rec["name"], _ask(f"New password for {rec['name']}: "), rec["role"])
        print("Password changed. Restart the server to sign that user out of open sessions.")
    elif args.cmd == "remove":
        print("Removed." if store.remove_user(args.name) else f"No user '{args.name}'.")


if __name__ == "__main__":
    main()
