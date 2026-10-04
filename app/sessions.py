import hashlib
import os
import secrets
import sqlite3
import time
from contextlib import closing, contextmanager
from pathlib import Path

from fastapi import Response
from starlette.requests import HTTPConnection

SESSION_COOKIE = "lesson_session"
LESSON_SESSION_TTL_SECONDS = int(os.getenv("LESSON_SESSION_TTL_SECONDS", "21600"))
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() in ("1", "true", "yes")
SESSION_DB_PATH = Path(os.getenv(
    "SESSION_DB_PATH", str(Path(__file__).resolve().parent.parent / "data" / "sessions.sqlite3")
))
if LESSON_SESSION_TTL_SECONDS <= 0:
    raise ValueError("LESSON_SESSION_TTL_SECONDS は正の整数にしてください。")


@contextmanager
def connection():
    SESSION_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(SESSION_DB_PATH)) as database:
        database.row_factory = sqlite3.Row
        database.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                name TEXT NOT NULL,
                expires_at REAL NOT NULL
            )
        """)
        with database:
            yield database


def get_session(token: str) -> dict | None:
    if not token:
        return None
    now = time.time()
    with connection() as database:
        database.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
        row = database.execute(
            "SELECT user_id, name, expires_at FROM sessions WHERE token_hash = ?",
            (hashlib.sha256(token.encode()).hexdigest(),),
        ).fetchone()
    return dict(row) if row else None


def current_user(request: HTTPConnection) -> dict | None:
    # HTTPとWebSocketのどちらも、同じCookieで参加者を確認する。
    return get_session(request.cookies.get(SESSION_COOKIE, ""))


def create_session(name: str, response: Response) -> None:
    token = secrets.token_urlsafe(32)
    now = time.time()
    with connection() as database:
        database.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
        # DBにはCookieのtokenそのものではなく、ハッシュを保存する。
        database.execute(
            "INSERT INTO sessions (token_hash, user_id, name, expires_at) VALUES (?, ?, ?, ?)",
            (hashlib.sha256(token.encode()).hexdigest(), "u_" + secrets.token_hex(8), name,
             now + LESSON_SESSION_TTL_SECONDS),
        )
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=LESSON_SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=COOKIE_SECURE,
    )
