import json
import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi import HTTPException, Response

from app import sessions as session_store


def test_session_is_stored_in_sqlite_without_the_raw_token(client):
    client.post("/app/sns/join", data={"name": "山田"})
    token = client.cookies.get(session_store.SESSION_COOKIE)
    with sqlite3.connect(session_store.SESSION_DB_PATH) as database:
        row = database.execute("SELECT token_hash, user_id, name, expires_at FROM sessions").fetchone()
    assert row[0] != token
    assert len(row[0]) == 64
    assert row[2] == "山田"
    assert token.encode() not in session_store.SESSION_DB_PATH.read_bytes()
    assert session_store.get_session(token)["user_id"] == row[1]


def test_session_can_be_loaded_by_a_new_python_process(client):
    client.post("/app/sns/join", data={"name": "山田"})
    token = client.cookies.get(session_store.SESSION_COOKIE)
    before = session_store.get_session(token)
    result = subprocess.run(
        [sys.executable, "-c", "import json, sys; from app import sessions; print(json.dumps(sessions.get_session(sys.stdin.read())))"],
        input=token,
        text=True,
        capture_output=True,
        check=True,
        env={**os.environ, "SESSION_DB_PATH": str(session_store.SESSION_DB_PATH)},
    )
    assert json.loads(result.stdout) == before


@pytest.mark.parametrize("token", ["", "unknown-token", "x' OR 1=1 --"])
def test_unknown_token_cannot_load_another_session(client, token):
    client.post("/app/sns/join", data={"name": "山田"})
    assert session_store.get_session(token) is None


def test_expired_rows_are_removed_from_the_database(client, monkeypatch):
    client.post("/app/sns/join", data={"name": "山田"})
    token = client.cookies.get(session_store.SESSION_COOKIE)
    deadline = session_store.get_session(token)["expires_at"]
    monkeypatch.setattr(session_store.time, "time", lambda: deadline)
    assert session_store.get_session(token) is None
    with sqlite3.connect(session_store.SESSION_DB_PATH) as database:
        assert database.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_concurrent_joins_reserve_a_name_once():
    ready = Barrier(2)

    def join():
        response = Response()
        ready.wait(timeout=5)
        try:
            session_store.create_session("山田", response, require_unique_name=True)
        except HTTPException as error:
            return error.status_code, "set-cookie" in response.headers
        return 200, "set-cookie" in response.headers

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: join(), range(2)))
    assert sorted(results) == [(200, True), (409, False)]
    with session_store.connection() as database:
        assert database.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1


def test_name_is_still_reserved_in_a_new_python_process(client):
    client.post("/app/sns/join", data={"name": "山田"})
    code = """
from fastapi import HTTPException, Response
from app.sessions import create_session

try:
    create_session("山田", Response(), require_unique_name=True)
except HTTPException as error:
    print(error.status_code)
else:
    print(200)
"""
    result = subprocess.run(
        [sys.executable, "-c", code], text=True, capture_output=True, check=True,
        env={**os.environ, "SESSION_DB_PATH": str(session_store.SESSION_DB_PATH)},
    )
    assert result.stdout.strip() == "409"
    with session_store.connection() as database:
        assert database.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1


def test_existing_duplicate_sessions_are_kept_but_new_sns_joins_are_rejected(client):
    # 変更前のDBには同名の参加情報が残っている場合がある。
    session_store.create_session("山田", Response())
    session_store.create_session("山田", Response())
    with session_store.connection() as database:
        original = [tuple(row) for row in database.execute("SELECT * FROM sessions ORDER BY user_id")]
    assert client.post("/app/sns/join", data={"name": "山田"}).status_code == 409
    with session_store.connection() as database:
        assert [tuple(row) for row in database.execute("SELECT * FROM sessions ORDER BY user_id")] == original
