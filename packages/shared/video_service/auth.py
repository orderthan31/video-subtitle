"""Local accounts and revocable sessions. Secrets never appear in job metadata."""
from contextlib import contextmanager
from functools import lru_cache
import hashlib
import hmac
from pathlib import Path
import re
import secrets
import sqlite3
import time
from threading import Lock
from uuid import uuid4


_kdf_lock = Lock()


def _derive(password, salt):
    # Bound concurrent 128 MiB scrypt allocations in the API process.
    with _kdf_lock:
        return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=131072, r=8, p=1, maxmem=256*1024**2)


def password_hash(password):
    if not isinstance(password, str) or not 12 <= len(password) <= 256:
        raise ValueError("Password must contain 12 to 256 characters")
    salt = secrets.token_bytes(16)
    digest = _derive(password, salt)
    return f"scrypt-v1${salt.hex()}${digest.hex()}"


def verify_password(password, encoded):
    if not isinstance(password, str) or not 12 <= len(password) <= 256:
        return False
    try:
        version, salt, expected = encoded.split("$")
        if version != "scrypt-v1" or len(salt) != 32 or len(expected) != 128:
            return False
        actual = _derive(password, bytes.fromhex(salt))
        return hmac.compare_digest(actual, bytes.fromhex(expected))
    except (ValueError, TypeError, AttributeError):
        return False


@lru_cache(maxsize=1)
def dummy_hash():
    return password_hash(secrets.token_urlsafe(32))


class LoginThrottled(Exception):
    def __init__(self, retry_after):
        self.retry_after = retry_after


class AuthStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    password_hash TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1);
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                    csrf_token TEXT NOT NULL, expires_at REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
                CREATE TABLE IF NOT EXISTS login_limits (
                    bucket TEXT PRIMARY KEY, attempts INTEGER NOT NULL, expires_at REAL NOT NULL);
            """)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def create_user(self, username, password):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{2,63}", username):
            raise ValueError("Username must be 3 to 64 ASCII letters, digits, dots, underscores or hyphens")
        encoded = password_hash(password)
        user_id = uuid4().hex
        with self.connection() as db:
            db.execute("INSERT INTO users(id, username, password_hash) VALUES (?, ?, ?)", (user_id, username, encoded))
        return {"id": user_id, "username": username}

    def reset_password(self, username, password):
        encoded = password_hash(password)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
            if row is None:
                raise ValueError("User not found")
            db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (encoded, row["id"]))
            db.execute("DELETE FROM sessions WHERE user_id = ?", (row["id"],))

    def consume_login_attempt(self, address):
        now = time.time()
        bucket = hashlib.sha256(address.encode("utf-8")).hexdigest()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM login_limits WHERE expires_at <= ?", (now,))
            row = db.execute("SELECT attempts, expires_at FROM login_limits WHERE bucket = ?", (bucket,)).fetchone()
            if row and row["attempts"] >= 20:
                raise LoginThrottled(max(1, int(row["expires_at"] - now) + 1))
            if row:
                db.execute("UPDATE login_limits SET attempts = attempts + 1 WHERE bucket = ?", (bucket,))
            else:
                db.execute("INSERT INTO login_limits VALUES (?, 1, ?)", (bucket, now + 900))

    def login(self, username, password, address, lifetime=28800):
        self.consume_login_attempt(address)
        with self.connection() as db:
            row = db.execute("SELECT * FROM users WHERE username = ? AND active = 1", (username,)).fetchone()
        encoded = row["password_hash"] if row else dummy_hash()
        valid = verify_password(password, encoded)
        if not valid or row is None:
            return None
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
        expires = time.time() + lifetime
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT password_hash FROM users WHERE id = ? AND active = 1", (row["id"],)).fetchone()
            if current is None or current["password_hash"] != encoded:
                return None
            db.execute("DELETE FROM sessions WHERE expires_at <= ?", (time.time(),))
            db.execute("INSERT INTO sessions VALUES (?, ?, ?, ?)", (token_hash, row["id"], csrf, expires))
            db.execute("DELETE FROM sessions WHERE token_hash IN (SELECT token_hash FROM sessions WHERE user_id = ? ORDER BY expires_at DESC LIMIT -1 OFFSET 20)", (row["id"],))
        return {"token": token, "user": {"id": row["id"], "username": row["username"]}, "csrf_token": csrf, "expires_at": expires}

    def session(self, token):
        if not token or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            return None
        digest = hashlib.sha256(token.encode("ascii")).hexdigest()
        with self.connection() as db:
            row = db.execute("""SELECT users.id, users.username, sessions.csrf_token, sessions.expires_at
                FROM sessions JOIN users ON sessions.user_id = users.id
                WHERE sessions.token_hash = ? AND sessions.expires_at > ? AND users.active = 1""", (digest, time.time())).fetchone()
        if row is None:
            return None
        return {"user": {"id": row["id"], "username": row["username"]}, "csrf_token": row["csrf_token"], "expires_at": row["expires_at"]}

    def logout(self, token):
        if token:
            with self.connection() as db:
                db.execute("DELETE FROM sessions WHERE token_hash = ?", (hashlib.sha256(token.encode("utf-8")).hexdigest(),))
