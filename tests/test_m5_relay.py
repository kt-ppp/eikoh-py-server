import asyncio
from contextlib import suppress
import json
import socket
import struct
import threading

import httpx
import pytest
import uvicorn
import websockets

from app.apps.sns import realtime as sns_realtime, state as sns_state
from app.main import app
from m5_relay import server_bridge


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class MockM5Relay:
    """M5StickCPlus 1.1 スケッチと同一のバイナリプロトコルで動作するテスト用中継器。"""

    def __init__(self, *, token: str = "eikoh-server-v1", initial_credit: int = 3072) -> None:
        self.token = token
        self.initial_credit = initial_credit
        self.max_sessions = 10
        self.server_port = 0
        self.client_port = 0

        self._server_srv: asyncio.AbstractServer | None = None
        self._client_srv: asyncio.AbstractServer | None = None
        self._tunnel_writer: asyncio.StreamWriter | None = None
        self._tunnel_reader_task: asyncio.Task[None] | None = None
        self._tunnel_ready = asyncio.Event()
        self._next_sid = 1
        self._sessions: dict[int, tuple[asyncio.StreamWriter, asyncio.Queue[bytes | None], asyncio.Task[None]]] = {}
        self._client_tasks: set[asyncio.Task[None]] = set()

    async def start(self) -> None:
        self._server_srv = await asyncio.start_server(self._handle_server_conn, "127.0.0.1", 0)
        self.server_port = self._server_srv.sockets[0].getsockname()[1]
        self._client_srv = await asyncio.start_server(self._handle_client_conn, "127.0.0.1", 0)
        self.client_port = self._client_srv.sockets[0].getsockname()[1]

    async def wait_tunnel_ready(self, timeout: float = 5.0) -> None:
        await asyncio.wait_for(self._tunnel_ready.wait(), timeout=timeout)

    async def stop(self) -> None:
        if self._client_srv is not None:
            self._client_srv.close()
        if self._server_srv is not None:
            self._server_srv.close()
        for sid in list(self._sessions):
            await self._close_session(sid)
        for task in list(self._client_tasks):
            task.cancel()
        if self._client_tasks:
            await asyncio.gather(*list(self._client_tasks), return_exceptions=True)
        if self._tunnel_reader_task is not None:
            self._tunnel_reader_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._tunnel_reader_task
        if self._tunnel_writer is not None:
            self._tunnel_writer.close()
            with suppress(OSError):
                await self._tunnel_writer.wait_closed()
        if self._client_srv is not None:
            await self._client_srv.wait_closed()
        if self._server_srv is not None:
            await self._server_srv.wait_closed()

    async def _send_tunnel(self, frame_type: int, sid: int, payload: bytes = b"") -> None:
        if self._tunnel_writer is None:
            return
        self._tunnel_writer.write(server_bridge.encode_frame(frame_type, sid, payload))
        await self._tunnel_writer.drain()

    async def _handle_server_conn(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            ftype, sid, payload = await server_bridge.read_frame(reader)
        except (asyncio.IncompleteReadError, ValueError, OSError):
            writer.close()
            return

        if ftype != server_bridge.FRAME_HELLO or sid != 0 or payload != self.token.encode("utf-8"):
            writer.write(server_bridge.encode_frame(server_bridge.FRAME_HELLO_ERR, 0))
            await writer.drain()
            writer.close()
            return

        self._tunnel_writer = writer
        ack_payload = struct.pack("<HH", self.max_sessions, self.initial_credit)
        await self._send_tunnel(server_bridge.FRAME_HELLO_ACK, 0, ack_payload)
        self._tunnel_ready.set()
        self._tunnel_reader_task = asyncio.create_task(self._tunnel_rx_loop(reader))

    async def _tunnel_rx_loop(self, reader: asyncio.StreamReader) -> None:
        try:
            while True:
                ftype, sid, payload = await server_bridge.read_frame(reader)
                if ftype == server_bridge.FRAME_PING:
                    await self._send_tunnel(server_bridge.FRAME_PONG, 0)
                elif ftype == server_bridge.FRAME_DATA:
                    entry = self._sessions.get(sid)
                    if entry is not None:
                        await entry[1].put(payload)
                elif ftype == server_bridge.FRAME_CLOSE:
                    entry = self._sessions.get(sid)
                    if entry is not None:
                        await entry[1].put(None)
        except (asyncio.IncompleteReadError, OSError, ValueError):
            pass

    async def _handle_client_conn(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        current = asyncio.current_task()
        if current is not None:
            self._client_tasks.add(current)
        sid = self._next_sid
        self._next_sid += 1
        out_q: asyncio.Queue[bytes | None] = asyncio.Queue()

        async def pump_to_client() -> None:
            try:
                while True:
                    chunk = await out_q.get()
                    if chunk is None:
                        break
                    writer.write(chunk)
                    await writer.drain()
                    await self._send_tunnel(server_bridge.FRAME_ACK, sid, struct.pack("<H", len(chunk)))
            except (OSError, ConnectionError):
                pass
            finally:
                writer.close()
                self._sessions.pop(sid, None)

        tx_task = asyncio.create_task(pump_to_client())
        self._sessions[sid] = (writer, out_q, tx_task)
        await self._send_tunnel(server_bridge.FRAME_OPEN, sid)

        try:
            while True:
                data = await reader.read(1024)
                if not data:
                    break
                await self._send_tunnel(server_bridge.FRAME_DATA, sid, data)
        except (OSError, ConnectionError):
            pass
        finally:
            if sid in self._sessions:
                await self._send_tunnel(server_bridge.FRAME_CLOSE, sid)
            await tx_task
            if current is not None:
                self._client_tasks.discard(current)

    async def _close_session(self, sid: int) -> None:
        entry = self._sessions.pop(sid, None)
        if entry is None:
            return
        writer, out_q, tx_task = entry
        await out_q.put(None)
        tx_task.cancel()
        with suppress(asyncio.CancelledError):
            await tx_task
        writer.close()


class BackgroundUvicorn:
    def __init__(self, port: int) -> None:
        self.config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        self.server = uvicorn.Server(self.config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self) -> "BackgroundUvicorn":
        self.thread.start()
        while not self.server.started:
            pass
        return self

    def __exit__(self, *args) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=5)


def test_rewrite_initial_http_chunk_normalizes_plain_http_and_preserves_ws_and_raw():
    decided, out = server_bridge.rewrite_initial_http_chunk(bytearray(b"GET /"))
    assert not decided
    assert out == b""

    buf = bytearray(b"GET / HTTP/1.1\r\nHost: 192.168.4.1:8080\r\nConnection: keep-alive\r\n\r\n")
    decided, out = server_bridge.rewrite_initial_http_chunk(buf)
    assert decided
    assert b"Connection: keep-alive" not in out
    assert b"Connection: close\r\n\r\n" in out

    ws_req = bytearray(
        b"GET /app/sns/ws HTTP/1.1\r\n"
        b"Host: 192.168.4.1:8080\r\n"
        b"Upgrade: websocket\r\n"
        b"Connection: Upgrade\r\n\r\n"
    )
    decided, out = server_bridge.rewrite_initial_http_chunk(ws_req)
    assert decided
    assert out == bytes(ws_req)

    raw = bytearray(b"RAW-HELLO\n")
    decided, out = server_bridge.rewrite_initial_http_chunk(raw)
    assert decided
    assert out == b"RAW-HELLO\n"


def test_relay_end_to_end_http_cookies_and_sns_websockets():
    sns_state.posts.clear()
    sns_realtime.connections.clear()
    uvicorn_port = find_free_port()

    async def scenario() -> None:
        # 初期クレジットを意図的に512Bと小さくし、複数回のACKフロー制御を検証する
        m5 = MockM5Relay(token="test-secret", initial_credit=512)
        await m5.start()
        bridge = server_bridge.RelayBridgeClient(
            m5_host="127.0.0.1",
            m5_port=m5.server_port,
            target_host="127.0.0.1",
            target_port=uvicorn_port,
            token="test-secret",
        )
        bridge_task = asyncio.create_task(bridge.run_forever())
        try:
            await m5.wait_tunnel_ready()
            base_url = f"http://127.0.0.1:{m5.client_port}"

            async with httpx.AsyncClient(base_url=base_url, follow_redirects=False) as alice, \
                       httpx.AsyncClient(base_url=base_url, follow_redirects=False) as bob:
                index = await alice.get("/")
                assert index.status_code == 200
                assert "SNS" in index.text
                assert "人狼" in index.text

                join_a = await alice.post("/app/sns/join", data={"name": "アリス"})
                assert join_a.status_code == 303
                assert join_a.headers["location"] == "/app/sns/"

                join_b = await bob.post("/app/sns/join", data={"name": "ボブ"})
                assert join_b.status_code == 303

                cookie_a = alice.cookies.get("session")
                cookie_b = bob.cookies.get("session")
                assert cookie_a and cookie_b

                ws_url = f"ws://127.0.0.1:{m5.client_port}/app/sns/ws"
                origin = base_url
                async with websockets.connect(
                    ws_url,
                    origin=origin,
                    additional_headers={"Cookie": f"session={cookie_a}"},
                ) as ws_a, websockets.connect(
                    ws_url,
                    origin=origin,
                    additional_headers={"Cookie": f"session={cookie_b}"},
                ) as ws_b:
                    assert json.loads(await ws_a.recv()) == {"type": "posts", "posts": []}
                    assert json.loads(await ws_b.recv()) == {"type": "posts", "posts": []}

                    await ws_a.send(json.dumps({"action": "post", "text": "M5中継テスト"}))
                    msg_a = json.loads(await ws_a.recv())
                    msg_b = json.loads(await ws_b.recv())
                    assert msg_a == msg_b
                    assert msg_a["posts"][0]["text"] == "M5中継テスト"
                    assert json.loads(await ws_a.recv())["type"] == "ok"

                # 静的ファイルを含む並列HTTPリクエストと人狼WebSocketの検証
                assets = await asyncio.gather(
                    alice.get("/static/style.css"),
                    alice.get("/app/werewolf/static/room.js"),
                    bob.get("/app/sns/static/sns.js"),
                )
                assert all(resp.status_code == 200 for resp in assets)
                assert all(resp.headers.get("connection") == "close" for resp in assets)

                create_room = await alice.post("/app/werewolf/rooms", data={"name": "中継部屋", "capacity": "4"})
                assert create_room.status_code == 303
                room_code = create_room.headers["location"].rsplit("/", 1)[-1]

                join_room = await bob.post("/app/werewolf/rooms/join", data={"code": room_code})
                assert join_room.status_code == 303

                ww_url = f"ws://127.0.0.1:{m5.client_port}/app/werewolf/rooms/{room_code}/ws"
                async with websockets.connect(
                    ww_url,
                    origin=origin,
                    additional_headers={"Cookie": f"session={cookie_a}"},
                ) as ww_a, websockets.connect(
                    ww_url,
                    origin=origin,
                    additional_headers={"Cookie": f"session={cookie_b}"},
                ) as ww_b:
                    assert json.loads(await ww_a.recv())["type"] == "room"
                    assert json.loads(await ww_b.recv())["type"] == "room"
                    await ww_a.send(json.dumps({"action": "chat", "channel": "public", "text": "やあ"}))
                    update_a = json.loads(await ww_a.recv())
                    update_b = json.loads(await ww_b.recv())
                    assert update_a["room"]["chat"][0]["text"] == "やあ"
                    assert update_b["room"]["chat"][0]["text"] == "やあ"
                    assert json.loads(await ww_a.recv())["type"] == "ok"
        finally:
            bridge.stop()
            await bridge_task
            await m5.stop()

    with BackgroundUvicorn(uvicorn_port):
        asyncio.run(scenario())


def test_relay_rejects_bad_token_and_returns_502_when_target_down():
    async def scenario() -> None:
        m5 = MockM5Relay(token="correct-token")
        await m5.start()
        bad_bridge = server_bridge.RelayBridgeClient(
            m5_host="127.0.0.1",
            m5_port=m5.server_port,
            target_host="127.0.0.1",
            target_port=find_free_port(),
            token="wrong-token",
        )
        with pytest.raises(ValueError, match="ハンドシェイク失敗"):
            await bad_bridge.run_once()

        unused_port = find_free_port()
        good_bridge = server_bridge.RelayBridgeClient(
            m5_host="127.0.0.1",
            m5_port=m5.server_port,
            target_host="127.0.0.1",
            target_port=unused_port,
            token="correct-token",
        )
        task = asyncio.create_task(good_bridge.run_forever())
        try:
            await m5.wait_tunnel_ready()
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{m5.client_port}") as client:
                resp = await client.get("/")
                assert resp.status_code == 502
                assert "eikoh-py-server" in resp.text
        finally:
            good_bridge.stop()
            await task
            await m5.stop()

    asyncio.run(scenario())
