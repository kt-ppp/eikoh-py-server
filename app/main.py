import os
import secrets
import time
from pathlib import Path

import markdown
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

APP_DIR = Path(__file__).resolve().parent
SESSION_COOKIE = "lesson_session"
LESSON_SESSION_TTL_SECONDS = int(os.getenv("LESSON_SESSION_TTL_SECONDS", "21600"))
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() in ("1", "true", "yes")
if LESSON_SESSION_TTL_SECONDS <= 0:
    raise ValueError("LESSON_SESSION_TTL_SECONDS は正の整数にしてください。")

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
templates = Jinja2Templates(directory=APP_DIR / "templates")

# 1つのPythonプロセス内だけで共有する。再起動するとすべて消える。
sessions: dict[str, dict] = {}
posts: list[dict] = []


def current_user(request: Request) -> dict | None:
    now = time.time()
    for token, session in list(sessions.items()):
        if session["expires_at"] <= now:
            del sessions[token]
    # Cookieのtokenから現在のユーザーを取得する。期限は延長しない。
    return sessions.get(request.cookies.get(SESSION_COOKIE, ""))


def sns_page(
    request: Request,
    user: dict | None,
    error: str = "",
    name: str = "",
    text: str = "",
    status_code: int = 200,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="sns.html",
        context={"user": user, "posts": posts, "error": error, "name": name, "text": text},
        status_code=status_code,
    )


@app.get("/app/sns/", response_class=HTMLResponse)
async def sns(request: Request):
    return sns_page(request, current_user(request))


@app.post("/app/sns/join")
async def join(request: Request, name: str = Form("")):
    user = current_user(request)
    if user is not None:
        return RedirectResponse("/app/sns/", status_code=303)
    name = name.strip()
    if not 1 <= len(name) <= 30:
        return sns_page(request, None, "名前は1〜30文字で入力してください。", name=name, status_code=400)

    token = secrets.token_urlsafe(32)
    sessions[token] = {
        "user_id": "u_" + secrets.token_hex(8),
        "name": name,
        "expires_at": time.time() + LESSON_SESSION_TTL_SECONDS,
    }
    response = RedirectResponse("/app/sns/", status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=LESSON_SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=COOKIE_SECURE,
    )
    return response


@app.post("/app/sns/post")
async def post(request: Request, text: str = Form("")):
    user = current_user(request)
    if user is None:
        response = RedirectResponse("/app/sns/", status_code=303)
        response.delete_cookie(SESSION_COOKIE, httponly=True, samesite="lax", secure=COOKIE_SECURE)
        return response
    text = text.strip()
    if not 1 <= len(text) <= 500:
        return sns_page(request, user, "投稿は1〜500文字で入力してください。", text=text, status_code=400)

    posts.append({
        "id": posts[-1]["id"] + 1 if posts else 1,
        "user_id": user["user_id"],
        "name": user["name"],
        "text": text,
    })
    del posts[:-100]  # 古い投稿を取り除き、最新100件だけを残す。
    return RedirectResponse("/app/sns/", status_code=303)


@app.get("/about", response_class=HTMLResponse)
async def about(request: Request):
    source = (APP_DIR.parent / "docs" / "student.md").read_text(encoding="utf-8")
    return templates.TemplateResponse(
        request=request,
        name="about.html",
        context={"content": markdown.markdown(source, extensions=["fenced_code"])},
    )
