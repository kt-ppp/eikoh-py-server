import json

import pytest
from starlette.websockets import WebSocketDisconnect

from app import sessions as session_store
from app.apps.sns import realtime, state


def join(client, name):
    client.cookies.clear()
    client.post("/app/sns/join", data={"name": name})
    token = client.cookies.get(session_store.SESSION_COOKIE)
    return {"cookie": f"{session_store.SESSION_COOKIE}={token}", "origin": "http://testserver"}


def test_two_users_receive_posts_edits_and_deletions_live(client):
    headers_a = join(client, "山田")
    headers_b = join(client, "田中")
    with client.websocket_connect("/app/sns/ws", headers=headers_a) as a, client.websocket_connect("/app/sns/ws", headers=headers_b) as b:
        assert a.receive_json() == b.receive_json() == {"type": "posts", "posts": []}
        a.send_json({"action": "post", "text": "こんにちは"})
        update = a.receive_json()
        assert b.receive_json() == update
        post_id = update["posts"][0]["id"]
        assert a.receive_json() == {"type": "ok", "action": "post", "id": post_id}
        assert update["posts"][0]["text"] == "こんにちは"

        a.send_json({"action": "edit", "id": post_id, "text": "こんばんは"})
        update = a.receive_json()
        assert b.receive_json() == update
        assert update["posts"][0]["text"] == "こんばんは"
        assert a.receive_json()["action"] == "edit"

        a.send_json({"action": "delete", "id": post_id})
        assert a.receive_json() == b.receive_json() == {"type": "posts", "posts": []}
        assert a.receive_json()["action"] == "delete"
    assert not realtime.connections


@pytest.mark.parametrize("action", ["edit", "delete"])
def test_websocket_rejects_other_users_mutations(client, action):
    join(client, "山田")
    client.post("/app/sns/post", data={"text": "本人の投稿"})
    post_id = state.posts[0]["id"]
    owner_id = state.posts[0]["user_id"]
    headers_b = join(client, "山田")
    with client.websocket_connect("/app/sns/ws", headers=headers_b) as b:
        b.receive_json()
        b.send_json({"action": action, "id": post_id, "text": "改ざん", "user_id": owner_id})
        error = b.receive_json()
        assert error["type"] == "error"
        assert error["status"] == 403
    assert state.posts[0]["text"] == "本人の投稿"


@pytest.mark.parametrize("raw", [
    "{", "null", "[]", json.dumps({"action": "unknown"}),
    json.dumps({"action": "post", "text": "  "}),
    json.dumps({"action": "post", "text": "あ" * 501}),
    json.dumps({"action": "post", "text": {"html": "x"}}),
    json.dumps({"action": "edit", "id": True, "text": "x"}),
])
def test_bad_messages_do_not_close_the_connection_or_change_posts(client, raw):
    headers = join(client, "山田")
    with client.websocket_connect("/app/sns/ws", headers=headers) as websocket:
        websocket.receive_json()
        websocket.send_text(raw)
        assert websocket.receive_json()["status"] == 400
        assert not state.posts
        websocket.send_json({"action": "post", "text": "正しい投稿"})
        assert websocket.receive_json()["posts"][0]["text"] == "正しい投稿"
        assert websocket.receive_json()["type"] == "ok"


def test_binary_frames_are_rejected(client):
    headers = join(client, "山田")
    with client.websocket_connect("/app/sns/ws", headers=headers) as websocket:
        websocket.receive_json()
        websocket.send_bytes(b"data")
        assert websocket.receive_json()["status"] == 400


@pytest.mark.parametrize("cookie", ["", "lesson_session=invalid"])
def test_unjoined_connections_receive_no_posts(client, cookie):
    with client.websocket_connect("/app/sns/ws", headers={"cookie": cookie}) as websocket:
        with pytest.raises(WebSocketDisconnect) as error:
            websocket.receive_json()
        assert error.value.code == 1008


@pytest.mark.parametrize("origin", ["https://evil.example", "null", "http://testserver:9999", "http://["])
def test_cross_origin_connections_are_rejected(client, origin):
    headers = join(client, "山田")
    headers["origin"] = origin
    with pytest.raises(WebSocketDisconnect) as error:
        with client.websocket_connect("/app/sns/ws", headers=headers):
            pass
    assert error.value.code == 1008


def test_expired_connection_cannot_modify_posts(client, monkeypatch):
    headers = join(client, "山田")
    token = client.cookies.get(session_store.SESSION_COOKIE)
    deadline = session_store.get_session(token)["expires_at"]
    with client.websocket_connect("/app/sns/ws", headers=headers) as websocket:
        websocket.receive_json()
        monkeypatch.setattr(session_store.time, "time", lambda: deadline)
        websocket.send_json({"action": "post", "text": "期限切れ"})
        with pytest.raises(WebSocketDisconnect) as error:
            websocket.receive_json()
        assert error.value.code == 1008
    assert not state.posts


def test_idle_connection_is_closed_at_session_expiry(client, monkeypatch):
    monkeypatch.setattr(session_store, "LESSON_SESSION_TTL_SECONDS", 1)
    headers = join(client, "山田")
    with client.websocket_connect("/app/sns/ws", headers=headers) as websocket:
        websocket.receive_json()
        with pytest.raises(WebSocketDisconnect) as error:
            websocket.receive_json()
        assert error.value.code == 1008


def test_http_forms_also_notify_websocket_subscribers(client):
    headers_a = join(client, "山田")
    headers_b = join(client, "田中")
    with client.websocket_connect("/app/sns/ws", headers=headers_b) as b:
        b.receive_json()
        client.post("/app/sns/post", data={"text": "HTTPから投稿"}, headers=headers_a)
        post_id = b.receive_json()["posts"][0]["id"]
        client.post(f"/app/sns/posts/{post_id}/edit", data={"text": "HTTPから編集"}, headers=headers_a)
        assert b.receive_json()["posts"][0]["text"] == "HTTPから編集"
        client.post(f"/app/sns/posts/{post_id}/delete", headers=headers_a)
        assert b.receive_json()["posts"] == []
