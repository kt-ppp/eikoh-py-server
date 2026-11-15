import hashlib
import os
import secrets
import sqlite3
import threading
import time
from contextlib import closing, contextmanager
from pathlib import Path

from fastapi import HTTPException, Response
from starlette.requests import HTTPConnection

SESSION_COOKIE = "session"
SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", "21600"))
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() in ("1", "true", "yes")
SESSION_DB_PATH = Path(
    os.getenv("SESSION_DB_PATH", "").strip()
    or str(Path(__file__).resolve().parent.parent / "data" / "sessions.sqlite3")
)
_DB_LOCK = threading.Lock()
if SESSION_TTL_SECONDS <= 0:
    raise ValueError("SESSION_TTL_SECONDS は正の整数にしてください。")

#DBに接続
@contextmanager
def connection():
    SESSION_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _DB_LOCK:
        with closing(sqlite3.connect(SESSION_DB_PATH, timeout=30.0)) as database:
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


#クライアントのセッションを確認し、ログイン情報を取得する
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
    return get_session(request.cookies.get(SESSION_COOKIE, ""))


#セッションを作成
def create_session(name: str, response: Response, *, require_unique_name: bool = False) -> None:
    token = secrets.token_urlsafe(32)
    now = time.time()
    with connection() as database:
        database.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
        # DELETEで書き込みロックを取得し、同時参加による確認と登録の競合を防ぐ。
        if require_unique_name and database.execute(
            "SELECT 1 FROM sessions WHERE name = ? LIMIT 1", (name,),
        ).fetchone():
            raise HTTPException(409, "その名前は使われています。別の名前を入力してください。")
        database.execute(
            "INSERT INTO sessions (token_hash, user_id, name, expires_at) VALUES (?, ?, ?, ?)",
            (hashlib.sha256(token.encode()).hexdigest(), "u_" + secrets.token_hex(8), name,
             now + SESSION_TTL_SECONDS),
        )
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=COOKIE_SECURE,
    )
