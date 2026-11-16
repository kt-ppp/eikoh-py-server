"""M5StickCPlus 1.1 リバーストンネル用サーバ側中継スクリプト。

サーバPC上の eikoh-py-server (例: 127.0.0.1:8080) と
M5StickCPlus 1.1 の中継待受ポート (例: 192.168.4.1:9000) を接続する。
サーバPCもクライアントPCもマイコン宛てにアウトバウンド接続のみを行うため、
PC側での着信ポート待ち受け・ファイアウォール許可が不要になる。
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
import logging
import os
import socket
import struct
import sys
import time

FRAME_HELLO = 0x01
FRAME_HELLO_ACK = 0x02
FRAME_HELLO_ERR = 0x03
FRAME_PING = 0x04
FRAME_PONG = 0x05
FRAME_OPEN = 0x10
FRAME_DATA = 0x11
FRAME_ACK = 0x12
FRAME_CLOSE = 0x13

HEADER_STRUCT = struct.Struct("<BHH")
HELLO_ACK_STRUCT = struct.Struct("<HH")
ACK_STRUCT = struct.Struct("<H")

DEFAULT_M5_HOST = "192.168.4.1"
DEFAULT_M5_PORT = 9000
DEFAULT_TARGET_HOST = "127.0.0.1"
DEFAULT_TARGET_PORT = 8080
DEFAULT_TOKEN = "eikoh-server-v1"

MAX_CHUNK_SIZE = 1024
PING_INTERVAL_SECONDS = 5.0
IDLE_TIMEOUT_SECONDS = 15.0
HANDSHAKE_TIMEOUT_SECONDS = 5.0
RECONNECT_DELAY_SECONDS = 1.5
MAX_HEADER_BUFFER_BYTES = 16384

HTTP_METHODS = (
    b"GET ",
    b"POST ",
    b"HEAD ",
    b"PUT ",
    b"DELETE ",
    b"OPTIONS ",
    b"PATCH ",
)

HTTP_502_RESPONSE = (
    b"HTTP/1.1 502 Bad Gateway\r\n"
    b"Content-Type: text/html; charset=utf-8\r\n"
    b"Connection: close\r\n"
    b"\r\n"
    b"<!doctype html><html lang=\"ja\"><head><meta charset=\"utf-8\">"
    b"<title>502 \xe3\x82\xb5\xe3\x83\xbc\xe3\x83\x90\xe6\x9c\xaa\xe8\xb5\xb7\xe5\x8b\x95</title></head><body>"
    b"<h1>\xe3\x83\xad\xe3\x83\xbc\xe3\x82\xab\xe3\x83\xab\xe3\x82\xb5\xe3\x83\xbc\xe3\x83\x90\xe3\x81\xab\xe6\x8e\xa5\xe7\xb6\x9a\xe3\x81\xa7\xe3\x81\x8d\xe3\x81\xbe\xe3\x81\x9b\xe3\x82\x93 (502)</h1>"
    b"<p>\xe3\x82\xb5\xe3\x83\xbc\xe3\x83\x90PC\xe4\xb8\x8a\xe3\x81\xa7 eikoh-py-server \xe3\x81\x8c\xe8\xb5\xb7\xe5\x8b\x95\xe3\x81\x97\xe3\x81\xa6\xe3\x81\x84\xe3\x82\x8b\xe3\x81\x8b\xe7\xa2\xba\xe8\xaa\x8d\xe3\x81\x97\xe3\x81\xa6\xe3\x81\x8f\xe3\x81\xa0\xe3\x81\x95\xe3\x81\x84\xe3\x80\x82</p>"
    b"</body></html>"
)

logger = logging.getLogger("m5_relay")


def encode_frame(frame_type: int, session_id: int, payload: bytes = b"") -> bytes:
    """5バイトヘッダ付きトンネルフレームをシリアライズする。"""
    return HEADER_STRUCT.pack(frame_type, session_id, len(payload)) + payload


async def read_frame(reader: asyncio.StreamReader) -> tuple[int, int, bytes]:
    """トンネルソケットから1フレームを読み取る。"""
    header = await reader.readexactly(HEADER_STRUCT.size)
    frame_type, session_id, length = HEADER_STRUCT.unpack(header)
    if length > MAX_CHUNK_SIZE and frame_type != FRAME_HELLO:
        raise ValueError(f"フレーム長が上限を超えています: {length}")
    payload = await reader.readexactly(length) if length > 0 else b""
    return frame_type, session_id, payload


def set_tcp_nodelay(writer: asyncio.StreamWriter) -> None:
    """Nagleアルゴリズムを無効化してWebSocket/HTTPの遅延を防ぐ。"""
    sock = writer.get_extra_info("socket")
    if sock is not None:
        with suppress(OSError):
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)


def rewrite_initial_http_chunk(buf: bytearray) -> tuple[bool, bytes]:
    """先頭HTTP/1.xリクエストの Connection ヘッダを close へ正規化する。

    ESP32側のソケット枠をブラウザのKeep-Aliveアイドル接続が占有しないようにする。
    WebSocketアップグレードや非HTTPストリームは一切改変せずそのまま透過する。

    Returns:
        (decided, bytes_to_flush)
        decided=False の場合はヘッダ終端を待つためまだ何も送信しない。
    """
    if not any(buf.startswith(method) or method.startswith(bytes(buf)) for method in HTTP_METHODS):
        return True, bytes(buf)

    newline_idx = buf.find(b"\n")
    if newline_idx >= 0 and b"HTTP/" not in buf[:newline_idx]:
        return True, bytes(buf)

    if len(buf) > MAX_HEADER_BUFFER_BYTES:
        return True, bytes(buf)

    header_end = buf.find(b"\r\n\r\n")
    if header_end < 0:
        return False, b""

    head = bytes(buf[:header_end])
    rest = bytes(buf[header_end + 4 :])
    lines = head.split(b"\r\n")
    if not lines or b"HTTP/" not in lines[0]:
        return True, bytes(buf)

    has_upgrade = any(line.lower().startswith(b"upgrade:") for line in lines[1:])
    if has_upgrade:
        return True, head + b"\r\n\r\n" + rest

    filtered = [line for line in lines[1:] if not line.lower().startswith(b"connection:")]
    filtered.append(b"Connection: close")
    rebuilt = b"\r\n".join([lines[0], *filtered]) + b"\r\n\r\n" + rest
    return True, rebuilt


@dataclass
class BridgeSession:
    """トンネル上で多重化される単一のクライアント接続状態。"""

    session_id: int
    initial_credit: int
    keep_alive: bool
    tx_credit: int = field(init=False)
    credit_event: asyncio.Event = field(default_factory=asyncio.Event)
    to_target_queue: asyncio.Queue[bytes | None] = field(default_factory=asyncio.Queue)
    closed: bool = False
    header_decided: bool = field(init=False)
    header_buf: bytearray = field(default_factory=bytearray)
    task: asyncio.Task[None] | None = None

    def __post_init__(self) -> None:
        self.tx_credit = self.initial_credit
        self.header_decided = self.keep_alive
        if self.tx_credit > 0:
            self.credit_event.set()

    def add_credit(self, amount: int) -> None:
        self.tx_credit += amount
        if self.tx_credit > 0:
            self.credit_event.set()


class RelayBridgeClient:
    """M5StickCPlus 1.1 の中継トンネルへ接続し、ローカルサーバと橋渡しするクライアント。"""

    def __init__(
        self,
        *,
        m5_host: str = DEFAULT_M5_HOST,
        m5_port: int = DEFAULT_M5_PORT,
        target_host: str = DEFAULT_TARGET_HOST,
        target_port: int = DEFAULT_TARGET_PORT,
        token: str = DEFAULT_TOKEN,
        keep_alive: bool = False,
    ) -> None:
        self.m5_host = m5_host
        self.m5_port = m5_port
        self.target_host = target_host
        self.target_port = target_port
        self.token = token
        self.keep_alive = keep_alive

        self._sessions: dict[int, BridgeSession] = {}
        self._send_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._initial_credit = 3072
        self._max_sessions = 10
        self._last_rx_monotonic = 0.0
        self._stop_event = asyncio.Event()

    def stop(self) -> None:
        self._stop_event.set()

    async def enqueue_frame(self, frame_type: int, session_id: int, payload: bytes = b"") -> None:
        await self._send_queue.put(encode_frame(frame_type, session_id, payload))

    async def run_forever(self) -> None:
        """切断時に自動再接続しながらトンネルを維持する。"""
        while not self._stop_event.is_set():
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError, ValueError) as exc:
                logger.warning("トンネル接続が終了しました: %s", exc)
            if self._stop_event.is_set():
                break
            logger.info("%.1f秒後に %s:%d へ再接続します...", RECONNECT_DELAY_SECONDS, self.m5_host, self.m5_port)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=RECONNECT_DELAY_SECONDS)
            except asyncio.TimeoutError:
                pass

    async def run_once(self) -> None:
        """1回のトンネルセッションを確立し、切断されるまで中継を実行する。"""
        logger.info("マイコンへセッション確立を要求中: %s:%d -> %s:%d",
                    self.m5_host, self.m5_port, self.target_host, self.target_port)
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(self.m5_host, self.m5_port),
            timeout=HANDSHAKE_TIMEOUT_SECONDS,
        )
        set_tcp_nodelay(writer)
        self._sessions.clear()
        self._send_queue = asyncio.Queue()

        try:
            writer.write(encode_frame(FRAME_HELLO, 0, self.token.encode("utf-8")))
            await asyncio.wait_for(writer.drain(), timeout=HANDSHAKE_TIMEOUT_SECONDS)

            frame_type, session_id, payload = await asyncio.wait_for(
                read_frame(reader),
                timeout=HANDSHAKE_TIMEOUT_SECONDS,
            )
            if frame_type == FRAME_HELLO_ERR or frame_type != FRAME_HELLO_ACK or session_id != 0:
                raise ValueError(f"ハンドシェイク失敗 (type=0x{frame_type:02X})")
            if len(payload) != HELLO_ACK_STRUCT.size:
                raise ValueError("HELLO_ACK のペイロード長が不正です")

            self._max_sessions, self._initial_credit = HELLO_ACK_STRUCT.unpack(payload)
            self._last_rx_monotonic = time.monotonic()
            logger.info(
                "中継セッションを確立しました (最大同時接続: %d, 初期ウィンドウ: %d B)",
                self._max_sessions,
                self._initial_credit,
            )

            writer_task = asyncio.create_task(self._tunnel_writer_loop(writer))
            ping_task = asyncio.create_task(self._ping_loop())
            reader_task = asyncio.create_task(self._tunnel_reader_loop(reader))
            stop_task = asyncio.create_task(self._stop_event.wait())

            done, pending = await asyncio.wait(
                {writer_task, ping_task, reader_task, stop_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                if task is not stop_task:
                    exc = task.exception()
                    if exc is not None:
                        raise exc
        finally:
            await self._cleanup_all_sessions()
            writer.close()
            with suppress(OSError):
                await writer.wait_closed()

    async def _cleanup_all_sessions(self) -> None:
        sessions = list(self._sessions.values())
        self._sessions.clear()
        for session in sessions:
            session.closed = True
            session.credit_event.set()
            with suppress(asyncio.QueueFull):
                session.to_target_queue.put_nowait(None)
            if session.task is not None:
                session.task.cancel()
        await asyncio.gather(
            *(session.task for session in sessions if session.task is not None),
            return_exceptions=True,
        )

    async def _tunnel_writer_loop(self, writer: asyncio.StreamWriter) -> None:
        while True:
            frame = await self._send_queue.get()
            if frame is None:
                return
            writer.write(frame)
            await writer.drain()

    async def _ping_loop(self) -> None:
        while True:
            await asyncio.sleep(PING_INTERVAL_SECONDS)
            if time.monotonic() - self._last_rx_monotonic > IDLE_TIMEOUT_SECONDS:
                raise asyncio.TimeoutError("マイコンからの応答が途絶えました")
            await self.enqueue_frame(FRAME_PING, 0)

    async def _tunnel_reader_loop(self, reader: asyncio.StreamReader) -> None:
        while True:
            frame_type, session_id, payload = await read_frame(reader)
            self._last_rx_monotonic = time.monotonic()

            if frame_type == FRAME_PING:
                await self.enqueue_frame(FRAME_PONG, 0)
            elif frame_type == FRAME_PONG:
                continue
            elif frame_type == FRAME_OPEN:
                self._handle_open(session_id)
            elif frame_type == FRAME_DATA:
                self._handle_data(session_id, payload)
            elif frame_type == FRAME_ACK:
                if len(payload) != ACK_STRUCT.size:
                    raise ValueError("ACK ペイロード長が不正です")
                (consumed,) = ACK_STRUCT.unpack(payload)
                session = self._sessions.get(session_id)
                if session is not None:
                    session.add_credit(consumed)
            elif frame_type == FRAME_CLOSE:
                self._handle_remote_close(session_id)
            else:
                logger.debug("未知のフレームを受信しました: type=0x%02X", frame_type)

    def _handle_open(self, session_id: int) -> None:
        if session_id in self._sessions:
            self._handle_remote_close(session_id)
        session = BridgeSession(
            session_id=session_id,
            initial_credit=self._initial_credit,
            keep_alive=self.keep_alive,
        )
        self._sessions[session_id] = session
        session.task = asyncio.create_task(self._run_session(session))

    def _handle_data(self, session_id: int, payload: bytes) -> None:
        session = self._sessions.get(session_id)
        if session is None or session.closed:
            return
        if not session.header_decided:
            session.header_buf.extend(payload)
            decided, flushed = rewrite_initial_http_chunk(session.header_buf)
            if not decided:
                return
            session.header_decided = True
            session.header_buf.clear()
            if flushed:
                session.to_target_queue.put_nowait(flushed)
            return
        session.to_target_queue.put_nowait(payload)

    def _handle_remote_close(self, session_id: int) -> None:
        session = self._sessions.get(session_id)
        if session is None:
            return
        session.closed = True
        session.credit_event.set()
        if not session.header_decided and session.header_buf:
            session.header_decided = True
            session.to_target_queue.put_nowait(bytes(session.header_buf))
            session.header_buf.clear()
        session.to_target_queue.put_nowait(None)

    async def _run_session(self, session: BridgeSession) -> None:
        local_reader: asyncio.StreamReader | None = None
        local_writer: asyncio.StreamWriter | None = None
        notify_close = True
        try:
            try:
                local_reader, local_writer = await asyncio.open_connection(
                    self.target_host, self.target_port
                )
            except OSError as exc:
                logger.warning("ローカルサーバ (%s:%d) への接続に失敗しました: %s",
                               self.target_host, self.target_port, exc)
                if not session.closed and session.tx_credit >= len(HTTP_502_RESPONSE):
                    await self._send_data_chunked(session, HTTP_502_RESPONSE)
                return

            set_tcp_nodelay(local_writer)
            up_task = asyncio.create_task(self._pump_to_target(session, local_writer))
            down_task = asyncio.create_task(self._pump_from_target(session, local_reader))
            done, pending = await asyncio.wait(
                {up_task, down_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            # ローカルサーバ側が終了した場合は残りのキュー出力を待たず速やかに閉じる
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                with suppress(asyncio.CancelledError):
                    exc = task.exception()
                    if exc is not None and not isinstance(exc, (OSError, ConnectionError)):
                        raise exc
            notify_close = not session.closed
        except asyncio.CancelledError:
            notify_close = False
            raise
        finally:
            if session.closed:
                notify_close = False
            self._sessions.pop(session.session_id, None)
            session.closed = True
            session.credit_event.set()
            if local_writer is not None:
                local_writer.close()
                with suppress(OSError):
                    await local_writer.wait_closed()
            if notify_close:
                with suppress(asyncio.CancelledError):
                    await self.enqueue_frame(FRAME_CLOSE, session.session_id)

    async def _pump_to_target(
        self, session: BridgeSession, local_writer: asyncio.StreamWriter
    ) -> None:
        while True:
            chunk = await session.to_target_queue.get()
            if chunk is None:
                return
            local_writer.write(chunk)
            await local_writer.drain()

    async def _pump_from_target(
        self, session: BridgeSession, local_reader: asyncio.StreamReader
    ) -> None:
        while not session.closed:
            while session.tx_credit <= 0:
                if session.closed:
                    return
                session.credit_event.clear()
                if session.tx_credit > 0 or session.closed:
                    break
                await session.credit_event.wait()

            max_read = min(MAX_CHUNK_SIZE, session.tx_credit)
            data = await local_reader.read(max_read)
            if not data:
                return
            session.tx_credit -= len(data)
            if session.tx_credit <= 0:
                session.credit_event.clear()
            await self.enqueue_frame(FRAME_DATA, session.session_id, data)

    async def _send_data_chunked(self, session: BridgeSession, data: bytes) -> None:
        for offset in range(0, len(data), MAX_CHUNK_SIZE):
            chunk = data[offset : offset + MAX_CHUNK_SIZE]
            if session.tx_credit < len(chunk):
                break
            session.tx_credit -= len(chunk)
            await self.enqueue_frame(FRAME_DATA, session.session_id, chunk)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="M5StickCPlus 1.1 の中継トンネルに接続し、ローカルの eikoh-py-server へ橋渡しします。"
    )
    parser.add_argument(
        "--m5-host",
        default=os.getenv("M5_HOST", DEFAULT_M5_HOST),
        help=f"M5StickCPlus 1.1 のIPアドレス (既定: {DEFAULT_M5_HOST})",
    )
    parser.add_argument(
        "--m5-port",
        type=int,
        default=int(os.getenv("M5_PORT", str(DEFAULT_M5_PORT))),
        help=f"M5StickCPlus 1.1 のサーバ接続用ポート (既定: {DEFAULT_M5_PORT})",
    )
    parser.add_argument(
        "--target-host",
        default=os.getenv("TARGET_HOST", DEFAULT_TARGET_HOST),
        help=f"ローカルサーバのホスト (既定: {DEFAULT_TARGET_HOST})",
    )
    parser.add_argument(
        "--target-port",
        type=int,
        default=int(os.getenv("TARGET_PORT", str(DEFAULT_TARGET_PORT))),
        help=f"ローカルサーバのポート (既定: {DEFAULT_TARGET_PORT})",
    )
    parser.add_argument(
        "--token",
        default=os.getenv("M5_TUNNEL_TOKEN", DEFAULT_TOKEN),
        help="M5StickCPlus 1.1 と共有する認証トークン",
    )
    parser.add_argument(
        "--keep-alive",
        action="store_true",
        help="通常HTTPリクエストへの Connection: close 付与を無効化する",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="デバッグログを表示する",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    client = RelayBridgeClient(
        m5_host=args.m5_host,
        m5_port=args.m5_port,
        target_host=args.target_host,
        target_port=args.target_port,
        token=args.token,
        keep_alive=args.keep_alive,
    )
    try:
        asyncio.run(client.run_forever())
    except KeyboardInterrupt:
        logger.info("終了しました。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
