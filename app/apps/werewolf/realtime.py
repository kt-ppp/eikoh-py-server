import asyncio
import json
import time
from contextlib import suppress
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from app import sessions
from app.apps.werewolf import state

router = APIRouter()
lock = asyncio.Lock()
connections: dict[WebSocket, tuple[str, str, str]] = {}


async def send(websocket: WebSocket, message: dict):
    await asyncio.wait_for(websocket.send_json(message), timeout=2)


async def publish(code: str):
    """lockを保持した呼び出し元から、本人用の状態を各接続へ送る。"""
    async def update(websocket: WebSocket, user_id: str, token: str):
        try:
            if sessions.get_session(token) is None:
                connections.pop(websocket, None)
                await websocket.close(code=1008, reason="参加情報が期限切れです。")
                return
            room = state.rooms.get(code)
            if room is None or user_id not in room.players:
                connections.pop(websocket, None)
                await send(websocket, {"type": "left"})
                await websocket.close()
                return
            await send(websocket, {"type": "room", "room": state.view(room, user_id), "server_time": time.time()})
        except (WebSocketDisconnect, RuntimeError, OSError, asyncio.TimeoutError):
            connections.pop(websocket, None)
            with suppress(WebSocketDisconnect, RuntimeError, OSError, asyncio.TimeoutError):
                await asyncio.wait_for(websocket.close(code=1011), timeout=1)

    await asyncio.gather(*(update(websocket, user_id, token)
                           for websocket, (room_code, user_id, token) in list(connections.items())
                           if room_code == code))


async def apply(code: str, user_id: str, data: dict) -> dict:
    async with lock:
        room = state.get_room(code)
        progressed = state.advance(room)
        try:
            state.command(room, user_id, data)
        except HTTPException:
            if progressed:
                await publish(room.code)
            raise
        await publish(room.code)
        return state.view(room, user_id)


async def run_clock():
    while True:
        await asyncio.sleep(0.5)
        async with lock:
            now = time.time()
            for room in list(state.rooms.values()):
                if room.phase in ("lobby", "finished") and now - room.updated_at > 21600:
                    del state.rooms[room.code]
                    await publish(room.code)
                elif state.advance(room, now):
                    await publish(room.code)
            # 操作のない接続でも、参加情報の失効を確認する。
            for websocket, (_, _, token) in list(connections.items()):
                if sessions.get_session(token) is None:
                    connections.pop(websocket, None)
                    with suppress(WebSocketDisconnect, RuntimeError, OSError, asyncio.TimeoutError):
                        await asyncio.wait_for(websocket.close(code=1008), timeout=1)


@router.websocket("/rooms/{code}/ws")
async def room_websocket(websocket: WebSocket, code: str):
    origin = websocket.headers.get("origin")
    if origin:
        try:
            source = urlsplit(origin)
            scheme = "https" if websocket.url.scheme == "wss" else "http"
            valid_origin = (source.scheme, source.netloc) == (scheme, websocket.url.netloc)
        except ValueError:
            valid_origin = False
        if not valid_origin:
            await websocket.close(code=1008)
            return
    user = sessions.current_user(websocket)
    await websocket.accept()
    if user is None:
        await websocket.close(code=1008, reason="名前を入力して参加してください。")
        return
    try:
        async with lock:
            room = state.get_room(code)
            state.member(room, user["user_id"])
            connections[websocket] = (room.code, user["user_id"], websocket.cookies[sessions.SESSION_COOKIE])
            if state.advance(room):
                await publish(room.code)
            else:
                await send(websocket, {"type": "room", "room": state.view(room, user["user_id"]), "server_time": time.time()})
    except HTTPException:
        await websocket.close(code=4004, reason="参加できるルームが見つかりません。")
        return

    token = websocket.cookies[sessions.SESSION_COOKIE]
    try:
        while True:
            try:
                raw = await websocket.receive_text()
            except KeyError:
                await send(websocket, {"type": "error", "message": "JSONをテキストで送信してください。", "status": 400})
                continue
            user = sessions.get_session(token)
            if user is None:
                await websocket.close(code=1008)
                break
            try:
                if len(raw) > 8192:
                    raise HTTPException(400, "操作のデータが大きすぎます。")
                command = json.loads(raw)
                await apply(room.code, user["user_id"], command)
            except (json.JSONDecodeError, ValueError):
                await send(websocket, {"type": "error", "status": 400, "message": "JSONが不正です。"})
                continue
            except HTTPException as error:
                await send(websocket, {"type": "error", "status": error.status_code, "message": error.detail})
                continue
            await send(websocket, {"type": "ok", "action": command["action"]})
    except (WebSocketDisconnect, RuntimeError, OSError, asyncio.TimeoutError):
        pass
    finally:
        connections.pop(websocket, None)
