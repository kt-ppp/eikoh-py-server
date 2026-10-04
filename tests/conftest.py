import pytest
from fastapi.testclient import TestClient

from app import sessions as session_store
from app.main import app


@pytest.fixture(autouse=True)
def session_database(tmp_path, monkeypatch):
    monkeypatch.setattr(session_store, "SESSION_DB_PATH", tmp_path / "sessions.sqlite3")
    yield


@pytest.fixture
def client():
    with TestClient(app) as browser:
        yield browser
