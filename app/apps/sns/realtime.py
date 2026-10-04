import asyncio
import json
import time
from contextlib import suppress
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from app import sessions as session_store
from app.apps.sns import state

router = APIRouter()
connections: dict[WebSocket, str] = {}
broadcast_lock = asyncio.Lock()


def snapshot() -> dict:
    return {"type": "posts", "posts": [post.copy() for post in state.posts]}


async def broadcast() -> None:
    # 同時に投稿された場合も、一覧の配信順序を揃える。
    async with broadcast_lock:
        message = snapshot()
        for websocket, token in list(connections.items()):
            try:
                if session_store.get_session(token) is None:
                    connections.pop(websocket, None)
                    await websocket.close(code=1008, reason="参加し直してください。")
                else:
                    await asyncio.wait_for(websocket.send_json(message), timeout=5)
            except (WebSocketDisconnect, RuntimeError, OSError, asyncio.TimeoutError):
                connections.pop(websocket, None)
                with suppress(WebSocketDisconnect, RuntimeError, OSError, asyncio.TimeoutError):
                    await asyncio.wait_for(websocket.close(code=1011), timeout=1)


@router.websocket("/ws")
async def websocket_sns(websocket: WebSocket):
    origin = websocket.headers.get("origin")
    expected_scheme = "https" if websocket.url.scheme == "wss" else "http"
    if origin:
        try:
            source = urlsplit(origin)
            same_origin = (source.scheme, source.netloc) == (expected_scheme, websocket.url.netloc)
        except ValueError:
            same_origin = False
        if not same_origin:
            await websocket.close(code=1008)
            return

    user = session_store.current_user(websocket)
    await websocket.accept()
    if user is None:
        await websocket.close(code=1008, reason="参加し直してください。")
        return

    token = websocket.cookies[session_store.SESSION_COOKIE]
    try:
        async with broadcast_lock:
            connections[websocket] = token
            await websocket.send_json(snapshot())

        while True:
            user = session_store.get_session(token)
            if user is None:
                await websocket.close(code=1008, reason="参加し直してください。")
                break
            try:
                raw = await asyncio.wait_for(
                    websocket.receive_text(), timeout=max(0, user["expires_at"] - time.time())
                )
            except asyncio.TimeoutError:
                await websocket.close(code=1008, reason="参加し直してください。")
                break
            except KeyError:
                await websocket.send_json({"type": "error", "status": 400, "message": "テキストで送ってください。"})
                continue

            # 接続が残っていても、期限切れのユーザーによる変更は受け付けない。
            user = session_store.get_session(token)
            if user is None:
                await websocket.close(code=1008, reason="参加し直してください。")
                break
            try:
                command = json.loads(raw)
                if not isinstance(command, dict):
                    raise HTTPException(400, "操作が不正です。")
                action = command.get("action")
                if action == "post":
                    post = state.add_post(user, command.get("text"))
                elif action == "edit":
                    post = state.edit_post(user, command.get("id"), command.get("text"))
                elif action == "delete":
                    post = state.delete_post(user, command.get("id"))
                else:
                    raise HTTPException(400, "操作が不正です。")
            except (json.JSONDecodeError, ValueError):
                await websocket.send_json({"type": "error", "status": 400, "message": "JSONが不正です。"})
                continue
            except HTTPException as error:
                await websocket.send_json({"type": "error", "status": error.status_code, "message": error.detail})
                continue

            await broadcast()
            await websocket.send_json({"type": "ok", "action": action, "id": post["id"]})
    except (WebSocketDisconnect, RuntimeError, OSError):
        pass
    finally:
        connections.pop(websocket, None)
