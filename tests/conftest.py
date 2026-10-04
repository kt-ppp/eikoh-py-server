import pytest
from fastapi.testclient import TestClient

from app import sessions as session_store
from app.main import app


@pytest.fixture(autouse=True)
def clear_sessions():
    session_store.sessions.clear()
    yield
    session_store.sessions.clear()


@pytest.fixture
def client():
    with TestClient(app) as browser:
        yield browser
