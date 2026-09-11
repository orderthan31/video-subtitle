"""Provision local accounts without passwords in arguments, history or output."""
import argparse
import getpass
import os
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages/shared"))
from video_service.auth import AuthStore
from video_service.config import load_environment


def main():
    load_environment()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["create", "reset-password"])
    parser.add_argument("username")
    parser.add_argument("--database", type=Path, default=Path(os.getenv("AUTH_DATABASE_PATH",
        str(Path(os.getenv("VIDEO_STORAGE_ROOT", "data/video-jobs")) / ".auth/accounts.sqlite3"))))
    args = parser.parse_args()
    password = getpass.getpass("New password (12-256 characters): ")
    if password != getpass.getpass("Confirm password: "):
        parser.error("Passwords do not match")
    try:
        store = AuthStore(args.database)
        if args.action == "create":
            user = store.create_user(args.username, password)
            print(f"Account created: {user['username']} ({user['id']})")
        else:
            store.reset_password(args.username, password)
            print("Password updated; all existing sessions revoked.")
    except sqlite3.IntegrityError:
        parser.error("Username already exists")
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
