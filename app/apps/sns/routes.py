from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app import sessions as session_store

APP_DIR = Path(__file__).resolve().parent
router = APIRouter(prefix="/app/sns")
templates = Jinja2Templates(directory=APP_DIR / "templates")

# SNSの投稿はこのアプリだけの状態としてメモリに保持する。
posts: list[dict] = []


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


@router.get("/", response_class=HTMLResponse)
async def sns(request: Request):
    return sns_page(request, session_store.current_user(request))


@router.post("/join")
async def join(request: Request, name: str = Form("")):
    user = session_store.current_user(request)
    if user is not None:
        return RedirectResponse("/app/sns/", status_code=303)
    name = name.strip()
    if not 1 <= len(name) <= 30:
        return sns_page(request, None, "名前は1〜30文字で入力してください。", name=name, status_code=400)

    response = RedirectResponse("/app/sns/", status_code=303)
    session_store.create_session(name, response)
    return response


@router.post("/post")
async def post(request: Request, text: str = Form("")):
    user = session_store.current_user(request)
    if user is None:
        response = RedirectResponse("/app/sns/", status_code=303)
        response.delete_cookie(
            session_store.SESSION_COOKIE,
            httponly=True,
            samesite="lax",
            secure=session_store.COOKIE_SECURE,
        )
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
