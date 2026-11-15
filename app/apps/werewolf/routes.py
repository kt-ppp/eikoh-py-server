import json
import time
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app import sessions
from app.apps.werewolf import realtime, state

APP_DIR = Path(__file__).resolve().parent
APP_TITLE = "人狼"
APP_DESCRIPTION = "4〜16人で遊ぶオンライン人狼"
router = APIRouter(prefix="/app/werewolf")
router.include_router(realtime.router)
templates = Jinja2Templates(directory=APP_DIR / "templates")


def home_page(request: Request, error: str = "", name: str = "", code: str = "", status: int = 200):
    user = sessions.current_user(request)
    return templates.TemplateResponse(
        request=request, name="home.html", status_code=status,
        context={"user": user, "name": name, "code": code, "error": error, "roles": state.ROLES,
                 "my_rooms": [room for room in state.rooms.values() if user and user["user_id"] in room.players]},
    )


@router.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return home_page(request)


@router.post("/join")
async def register(request: Request, name: str = Form(""), code: str = Form("")):
    code = code.strip().upper()
    if code and (len(code) != 6 or not code.isalnum() or not code.isascii()):
        return home_page(request, "ルームコードは6文字の英数字です。", name, status=400)
    response = RedirectResponse(f"/app/werewolf/rooms/{code}" if code else "/app/werewolf/", status_code=303)
    if sessions.current_user(request) is not None:
        return response
    name = name.strip()
    if not 1 <= len(name) <= 30:
        return home_page(request, "名前は1〜30文字で入力してください。", name, code, 400)
    try:
        sessions.create_session(name, response, require_unique_name=True)
    except HTTPException as error:
        return home_page(request, error.detail, name, code, error.status_code)
    return response


@router.post("/rooms")
async def create(request: Request, name: str = Form(""), capacity: int = Form(8)):
    user = sessions.current_user(request)
    if user is None:
        return RedirectResponse("/app/werewolf/", status_code=303)
    async with realtime.lock:
        try:
            room = state.create_room(user, name, {"capacity": capacity})
        except HTTPException as error:
            return home_page(request, error.detail, status=error.status_code)
    return RedirectResponse(f"/app/werewolf/rooms/{room.code}", status_code=303)


@router.post("/rooms/join")
async def join(request: Request, code: str = Form("")):
    user = sessions.current_user(request)
    if user is None:
        return home_page(request, "先に名前を入力してください。", code=code, status=401)
    async with realtime.lock:
        try:
            room = state.get_room(code)
            state.join_room(room, user)
        except HTTPException as error:
            return home_page(request, error.detail, code=code, status=error.status_code)
        await realtime.publish(room.code)
    return RedirectResponse(f"/app/werewolf/rooms/{room.code}", status_code=303)


@router.get("/rooms/{code}", response_class=HTMLResponse)
async def room_page(request: Request, code: str):
    try:
        room = state.get_room(code)
    except HTTPException as error:
        return home_page(request, error.detail, status=error.status_code)
    user = sessions.current_user(request)
    if user is None or user["user_id"] not in room.players:
        return home_page(request, code=room.code)
    async with realtime.lock:
        if state.advance(room):
            await realtime.publish(room.code)
        view = state.view(room, user["user_id"])
    response = templates.TemplateResponse(
        request=request, name="room.html",
        context={"user": user, "room": view, "roles": state.ROLES, "server_time": time.time()},
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/rooms/{code}/state")
async def room_state(request: Request, code: str):
    user = sessions.current_user(request)
    if user is None:
        raise HTTPException(401, "名前を入力して参加してください。")
    async with realtime.lock:
        room = state.get_room(code)
        state.member(room, user["user_id"])
        if state.advance(room):
            await realtime.publish(room.code)
        response = {"room": state.view(room, user["user_id"]), "server_time": time.time()}
    return JSONResponse(response, headers={"Cache-Control": "no-store"})


@router.post("/rooms/{code}/actions")
async def action(request: Request, code: str):
    user = sessions.current_user(request)
    if user is None:
        raise HTTPException(401, "名前を入力して参加してください。")
    raw = await request.body()
    if len(raw) > 8192:
        raise HTTPException(400, "操作のデータが大きすぎます。")
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(400, "JSONが不正です。")
    view = await realtime.apply(code, user["user_id"], data)
    return {"room": view, "server_time": time.time()}


@router.post("/rooms/{code}/leave")
async def leave(request: Request, code: str):
    user = sessions.current_user(request)
    if user is None:
        return RedirectResponse("/app/werewolf/", status_code=303)
    async with realtime.lock:
        room = state.get_room(code)
        state.leave_room(room, user["user_id"])
        await realtime.publish(room.code)
    return RedirectResponse("/app/werewolf/", status_code=303)
