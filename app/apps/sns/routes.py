from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app import sessions as session_store
from app.apps.sns import realtime, state

APP_DIR = Path(__file__).resolve().parent
router = APIRouter(prefix="/app/sns")
router.include_router(realtime.router)
templates = Jinja2Templates(directory=APP_DIR / "templates")

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
        context={"user": user, "posts": state.posts, "error": error, "name": name, "text": text},
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
    try:
        state.add_post(user, text)
    except HTTPException as error:
        return sns_page(request, user, error.detail, text=text, status_code=error.status_code)
    await realtime.broadcast()
    return RedirectResponse("/app/sns/", status_code=303)


@router.post("/posts/{post_id}/edit")
async def edit(request: Request, post_id: str, text: str = Form("")):
    user = session_store.current_user(request)
    if user is None:
        return RedirectResponse("/app/sns/", status_code=303)
    try:
        state.edit_post(user, post_id, text)
    except HTTPException as error:
        return sns_page(request, user, error.detail, status_code=error.status_code)
    await realtime.broadcast()
    return RedirectResponse("/app/sns/", status_code=303)


@router.post("/posts/{post_id}/delete")
async def delete(request: Request, post_id: str):
    user = session_store.current_user(request)
    if user is None:
        return RedirectResponse("/app/sns/", status_code=303)
    try:
        state.delete_post(user, post_id)
    except HTTPException as error:
        return sns_page(request, user, error.detail, status_code=error.status_code)
    await realtime.broadcast()
    return RedirectResponse("/app/sns/", status_code=303)
