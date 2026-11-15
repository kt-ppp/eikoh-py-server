import asyncio

import pytest
from fastapi.testclient import TestClient

from app import sessions as session_store
from app.main import app
from app.apps.werewolf import realtime as game_realtime, state as game_state


@pytest.fixture(autouse=True)
def clear_werewolf(monkeypatch):
    game_state.rooms.clear()
    game_realtime.connections.clear()
    monkeypatch.setattr(game_realtime, "lock", asyncio.Lock())
    yield
    game_state.rooms.clear()
    game_realtime.connections.clear()


@pytest.fixture(autouse=True)
def session_database(tmp_path, monkeypatch):
    monkeypatch.setattr(session_store, "SESSION_DB_PATH", tmp_path / "sessions.sqlite3")
    yield


@pytest.fixture
def client():
    with TestClient(app) as browser:
        yield browser
