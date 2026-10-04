import json
import os
import sqlite3
import subprocess
import sys

import pytest

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
