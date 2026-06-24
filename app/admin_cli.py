from __future__ import annotations

import argparse
import os
from pathlib import Path

from app.store import JsonStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Admin CLI for AVM Demo")
    parser.add_argument("--data-dir", default=os.getenv("VMS_DATA_DIR", "data"))
    sub = parser.add_subparsers(dest="command", required=True)

    reset = sub.add_parser("reset-trial")
    reset.add_argument("email")
    reset.add_argument("--days", type=int, default=7)

    delete = sub.add_parser("delete-user")
    delete.add_argument("email")

    args = parser.parse_args()
    store = JsonStore(Path(args.data_dir))

    if args.command == "reset-trial":
        user = store.reset_user_trial_by_email(args.email, days=args.days)
        if not user:
            raise SystemExit(f"user not found: {args.email}")
        print(f"trial reset: {user.email} until {user.trial_expires_at}")
        return

    if args.command == "delete-user":
        deleted = store.delete_user_by_email(args.email)
        if not deleted:
            raise SystemExit(f"user not found: {args.email}")
        print(f"user deleted: {args.email}")


if __name__ == "__main__":
    main()
