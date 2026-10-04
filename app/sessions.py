import os
import secrets
import time

from fastapi import Request, Response

SESSION_COOKIE = "lesson_session"
LESSON_SESSION_TTL_SECONDS = int(os.getenv("LESSON_SESSION_TTL_SECONDS", "21600"))
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() in ("1", "true", "yes")
if LESSON_SESSION_TTL_SECONDS <= 0:
    raise ValueError("LESSON_SESSION_TTL_SECONDS は正の整数にしてください。")

# アプリ間で共通の一時ユーザー。再起動するとすべて消える。
sessions: dict[str, dict] = {}


def current_user(request: Request) -> dict | None:
    now = time.time()
    for token, session in list(sessions.items()):
        if session["expires_at"] <= now:
            del sessions[token]
    # Cookieのtokenから現在のユーザーを取得する。期限は延長しない。
    return sessions.get(request.cookies.get(SESSION_COOKIE, ""))


def create_session(name: str, response: Response) -> None:
    token = secrets.token_urlsafe(32)
    sessions[token] = {
        "user_id": "u_" + secrets.token_hex(8),
        "name": name,
        "expires_at": time.time() + LESSON_SESSION_TTL_SECONDS,
    }
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=LESSON_SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=COOKIE_SECURE,
    )
