import asyncio

import pytest

from app.apps.sns import realtime, state


@pytest.fixture(autouse=True)
def clear_sns(monkeypatch):
    state.posts.clear()
    realtime.connections.clear()
    monkeypatch.setattr(realtime, "broadcast_lock", asyncio.Lock())
    yield
    state.posts.clear()
    realtime.connections.clear()
