import json
import re
import time
from contextlib import ExitStack

import pytest
from starlette.websockets import WebSocketDisconnect

from app import sessions
from app.apps.werewolf import state


def register(client, name):
    client.cookies.clear()
    response = client.post("/app/werewolf/join", data={"name": name}, follow_redirects=False)
    assert response.status_code == 303
    token = client.cookies.get(sessions.SESSION_COOKIE)
    return {"cookie": f"{sessions.SESSION_COOKIE}={token}", "origin": "http://testserver"}, sessions.get_session(token)


def players_in_room(client, count=4):
    headers, user = register(client, "参加者0")
    response = client.post("/app/werewolf/rooms", data={"name": "人狼ルーム", "capacity": 8}, headers=headers, follow_redirects=False)
    assert response.status_code == 303
    room = state.get_room(response.headers["location"].rsplit("/", 1)[-1])
    players = [(headers, user)]
    for index in range(1, count):
        headers, user = register(client, f"参加者{index}")
        assert client.post("/app/werewolf/rooms/join", data={"code": room.code.lower()}, headers=headers, follow_redirects=False).status_code == 303
        players.append((headers, user))
    return room, players


def act(client, room, headers, command):
    return client.post(f"/app/werewolf/rooms/{room.code}/actions", headers=headers, json=command)


def test_home_page_rules_and_shared_sns_session(client):
    response = client.get("/app/werewolf/")
    assert response.status_code == 200
    assert 'id="rules-dialog"' in response.text
    assert "GMはいりません" in response.text
    for role in state.ROLES.values():
        assert role["name"] in response.text
    client.post("/app/sns/join", data={"name": "共通ユーザー"})
    token = client.cookies.get(sessions.SESSION_COOKIE)
    response = client.get("/app/werewolf/")
    assert "共通ユーザー" in response.text
    assert 'action="/app/werewolf/rooms"' in response.text
    assert client.cookies.get(sessions.SESSION_COOKIE) == token


def test_create_join_configuration_permissions_and_actual_selected_roles(client):
    room, players = players_in_room(client, 7)
    assert len(room.players) == 7 and room.owner_id == players[0][1]["user_id"]
    settings = {"wolves": 2, "seer": 1, "guard": 1, "medium": 1, "madman": 1,
                "night_seconds": 5, "discussion_seconds": 10, "vote_seconds": 5}
    assert act(client, room, players[1][0], {"action": "configure", "settings": settings}).status_code == 403
    assert act(client, room, players[0][0], {"action": "configure", "settings": settings}).status_code == 200
    assert room.settings["night_seconds"] == 5
    assert act(client, room, players[1][0], {"action": "start"}).status_code == 403
    response = act(client, room, players[0][0], {"action": "start"})
    assert response.status_code == 200
    assert sorted(player.role for player in room.players.values()) == ["guard", "madman", "medium", "seer", "villager", "wolf", "wolf"]
    for headers, user in players:
        view = client.get(f"/app/werewolf/rooms/{room.code}/state", headers=headers).json()["room"]
        assert view["you"]["role"] == room.players[user["user_id"]].role
        assert all("role" not in player for player in view["players"])


def test_room_is_private_to_members_and_html_contains_only_own_secret(client):
    room, players = players_in_room(client)
    act(client, room, players[0][0], {"action": "start"})
    for index, (headers, user) in enumerate(players):
        state.private_record(room, user["user_id"], f"本人だけの結果{index}")
    for index, (headers, user) in enumerate(players):
        response = client.get(f"/app/werewolf/rooms/{room.code}", headers=headers)
        assert response.headers["cache-control"] == "no-store"
        data = json.loads(re.search(r'<script id="initial-room" type="application/json">(.*?)</script>', response.text, re.S)[1])["room"]
        assert data["private_log"] == [f"本人だけの結果{index}"]
        assert all("role" not in player for player in data["players"])
    stranger, _ = register(client, "部外者")
    assert client.get(f"/app/werewolf/rooms/{room.code}/state", headers=stranger).status_code == 403
    assert act(client, room, stranger, {"action": "start"}).status_code == 403
    assert client.get(f"/app/werewolf/rooms/{room.code}/state", headers={"cookie": ""}).status_code == 401
    assert act(client, room, {"cookie": ""}, {"action": "start"}).status_code == 401
    with client.websocket_connect(f"/app/werewolf/rooms/{room.code}/ws", headers=stranger) as websocket:
        with pytest.raises(WebSocketDisconnect) as error:
            websocket.receive_json()
        assert error.value.code == 4004


def test_websocket_lobby_updates_commands_and_errors(client):
    room, players = players_in_room(client, 2)
    url = f"/app/werewolf/rooms/{room.code}/ws"
    with client.websocket_connect(url, headers=players[0][0]) as a, client.websocket_connect(url, headers=players[1][0]) as b:
        assert a.receive_json()["room"]["code"] == b.receive_json()["room"]["code"] == room.code
        a.send_json({"action": "configure", "settings": {"night_seconds": 15}})
        assert a.receive_json()["room"]["settings"]["night_seconds"] == 15
        assert b.receive_json()["room"]["settings"]["night_seconds"] == 15
        assert a.receive_json() == {"type": "ok", "action": "configure"}
        b.send_json({"action": "configure", "settings": {"night_seconds": 5}, "user_id": room.owner_id})
        assert b.receive_json()["status"] == 403
        for raw in ("{", "null", "[]", json.dumps({"action": "unknown"})):
            b.send_text(raw)
            assert b.receive_json()["status"] == 400
        b.send_bytes(b"data")
        assert b.receive_json()["status"] == 400
        a.send_json({"action": "chat", "text": "相談しよう"})
        assert a.receive_json()["room"]["chat"][0]["text"] == b.receive_json()["room"]["chat"][0]["text"] == "相談しよう"
        assert a.receive_json()["type"] == "ok"
        a.send_json({"action": "start"})
        assert a.receive_json()["status"] == 400


def test_background_clock_runs_all_phases_and_ends_game_without_gm_or_actions(client):
    room, players = players_in_room(client)
    act(client, room, players[0][0], {"action": "start"})
    with client.websocket_connect(f"/app/werewolf/rooms/{room.code}/ws", headers=players[0][0]) as websocket:
        assert websocket.receive_json()["room"]["phase"] == "night"
        for expected in ("discussion", "vote", "night", "finished"):
            room.deadline = time.time() - 1
            frame = websocket.receive_json()
            assert frame["room"]["phase"] == expected
        assert frame["room"]["winner"] == "wolves"
        assert all("role" in player for player in frame["room"]["players"])


def test_clock_continues_after_all_clients_disconnect_and_same_user_can_reconnect(client):
    room, players = players_in_room(client)
    act(client, room, players[0][0], {"action": "start"})
    url = f"/app/werewolf/rooms/{room.code}/ws"
    with client.websocket_connect(url, headers=players[0][0]) as websocket:
        previous = websocket.receive_json()["room"]["you"]["role"]
    room.deadline = time.time() - 1
    limit = time.monotonic() + 3
    while room.phase == "night" and time.monotonic() < limit:
        time.sleep(0.05)
    assert room.phase == "discussion"
    with client.websocket_connect(url, headers=players[0][0]) as websocket:
        view = websocket.receive_json()["room"]
        assert view["you"]["role"] == previous
        assert view["phase"] == "discussion"


@pytest.mark.parametrize("origin", ["https://evil.example", "null", "http://testserver:1234", "http://["])
def test_cross_origin_websocket_is_rejected(client, origin):
    room, players = players_in_room(client, 1)
    headers = {**players[0][0], "origin": origin}
    with pytest.raises(WebSocketDisconnect) as error:
        with client.websocket_connect(f"/app/werewolf/rooms/{room.code}/ws", headers=headers):
            pass
    assert error.value.code == 1008


def test_expired_socket_cannot_act_and_expired_session_cannot_load_room(client):
    room, players = players_in_room(client, 1)
    headers, user = players[0]
    with client.websocket_connect(f"/app/werewolf/rooms/{room.code}/ws", headers=headers) as websocket:
        websocket.receive_json()
        with sessions.connection() as database:
            database.execute("UPDATE sessions SET expires_at = ? WHERE user_id = ?", (time.time() - 1, user["user_id"]))
        websocket.send_json({"action": "chat", "text": "期限切れ"})
        with pytest.raises(WebSocketDisconnect) as error:
            websocket.receive_json()
        assert error.value.code == 1008
    assert not room.chat
    assert client.get(f"/app/werewolf/rooms/{room.code}/state", headers=headers).status_code == 401


def test_capacity_membership_and_leave_transfer(client):
    room, players = players_in_room(client)
    assert act(client, room, players[0][0], {"action": "configure", "settings": {"capacity": 4}}).status_code == 200
    stranger, _ = register(client, "満員後")
    assert client.post("/app/werewolf/rooms/join", headers=stranger, data={"code": room.code}).status_code == 409
    assert client.post(f"/app/werewolf/rooms/{room.code}/leave", headers=players[0][0], follow_redirects=False).status_code == 303
    assert room.owner_id == players[1][1]["user_id"]
    assert len(room.players) == 3


def test_share_link_requests_name_and_prepopulates_code(client):
    room, _ = players_in_room(client, 1)
    client.cookies.clear()
    response = client.get(f"/app/werewolf/rooms/{room.code}")
    assert f'name="code" value="{room.code}"' in response.text
    response = client.post("/app/werewolf/join", data={"name": "リンクから", "code": room.code}, follow_redirects=False)
    assert response.headers["location"] == f"/app/werewolf/rooms/{room.code}"
    response = client.get(response.headers["location"])
    assert f'value="{room.code}"' in response.text
    assert 'action="/app/werewolf/rooms/join"' in response.text


def test_websocket_night_actions_and_unanimous_skip_preserve_private_state(client):
    room, players = players_in_room(client)
    act(client, room, players[0][0], {"action": "start"})
    for player, role in zip(room.players.values(), ("wolf", "seer", "guard", "villager")):
        player.role = role
    ids = [user["user_id"] for _, user in players]
    with ExitStack() as stack:
        sockets = [stack.enter_context(client.websocket_connect(f"/app/werewolf/rooms/{room.code}/ws", headers=headers))
                   for headers, _ in players]
        for socket in sockets:
            assert socket.receive_json()["room"]["phase"] == "night"

        def exchange(index, data):
            sockets[index].send_json(data)
            views = [socket.receive_json()["room"] for socket in sockets]
            assert sockets[index].receive_json() == {"type": "ok", "action": data["action"]}
            return views

        phase_id = room.phase_id
        sockets[3].send_json({"action": "skip", "phase_id": phase_id, "agreed": True})
        assert sockets[3].receive_json()["status"] == 409
        for index, target in enumerate((ids[3], ids[0], ids[3], ids[0])):
            views = exchange(index, {"action": "night", "phase_id": phase_id, "target": target})
            for other_index, view in enumerate(views):
                assert view["you"]["selection"] == room.actions.get(ids[other_index])
                assert all(set(player) == {"user_id", "name", "alive", "voted"} for player in view["players"])
                assert not view["private_log"]
        for index in range(3):
            views = exchange(index, {"action": "skip", "phase_id": phase_id, "agreed": True, "user_id": ids[3]})
            assert all(view["phase"] == "night" and view["skip"]["agreed"] == index + 1 for view in views)
            assert not views[3]["skip"]["you_agreed"]
        response = client.get(f"/app/werewolf/rooms/{room.code}/state", headers=players[0][0])
        assert response.json()["room"]["skip"]["you_agreed"] is True
        views = exchange(3, {"action": "skip", "phase_id": phase_id, "agreed": True})
        assert all(view["phase"] == "discussion" and view["skip"]["agreed"] == 0 for view in views)
        assert "人狼です" in views[1]["private_log"][0]
        assert all(not views[index]["private_log"] for index in (0, 2, 3))
        assert all(player.alive for player in room.players.values())
        phase_id = room.phase_id
        exchange(0, {"action": "skip", "phase_id": phase_id, "agreed": True})
        views = exchange(0, {"action": "skip", "phase_id": phase_id, "agreed": False})
        assert all(view["skip"]["agreed"] == 0 and view["phase"] == "discussion" for view in views)
        for index in range(4):
            views = exchange(index, {"action": "skip", "phase_id": phase_id, "agreed": True})
        assert all(view["phase"] == "vote" and view["skip"]["agreed"] == 0 for view in views)
        sockets[0].send_json({"action": "skip", "phase_id": phase_id, "agreed": True})
        assert sockets[0].receive_json()["status"] == 409
        assert not room.skip_agreements
        phase_id = room.phase_id
        exchange(1, {"action": "vote", "phase_id": phase_id, "target": ids[0]})
        for index in range(4):
            views = exchange(index, {"action": "skip", "phase_id": phase_id, "agreed": True})
        assert all(view["phase"] == "finished" and view["winner"] == "village" for view in views)
        assert all(all("role" in player for player in view["players"]) for view in views)


def test_http_skip_sent_after_deadline_cannot_agree_to_the_next_phase(client):
    room, players = players_in_room(client)
    act(client, room, players[0][0], {"action": "start"})
    state.begin_phase(room, "discussion", time.time())
    previous_phase = room.phase_id
    room.deadline = time.time() - 1
    response = act(client, room, players[0][0], {"action": "skip", "phase_id": previous_phase, "agreed": True})
    assert response.status_code == 409
    assert room.phase == "vote" and not room.skip_agreements
