import random

import pytest
from fastapi import HTTPException

from app.apps.werewolf import state


def game(roles=None):
    users = [{"user_id": f"u_{index}", "name": f"参加者{index}"} for index in range(4 if roles is None else len(roles))]
    room = state.create_room(users[0], "テストルーム", {"capacity": max(4, len(users))})
    for user in users[1:]:
        state.join_room(room, user)
    state.command(room, users[0]["user_id"], {"action": "start"}, now=100)
    if roles:
        for player, role in zip(room.players.values(), roles):
            player.role = role
    return room, users


def test_creator_plays_and_roles_are_randomly_assigned(monkeypatch):
    monkeypatch.setattr(state, "random", random.Random(5))
    room, users = game()
    assert room.owner_id in room.players
    assert sorted(player.role for player in room.players.values()) == ["guard", "seer", "villager", "wolf"]
    assert room.phase == "night"
    assert room.day == 1
    assert room.deadline == 160
    assert all(player.alive for player in room.players.values())
    assert len({user["user_id"] for user in users}) == 4


@pytest.mark.parametrize("settings", [
    {"capacity": 3}, {"capacity": 17}, {"wolves": 4}, {"seer": 2}, {"guard": True},
    {"night_seconds": 4}, {"discussion_seconds": 1801}, {"vote_seconds": "10"},
    {"capacity": 4, "seer": 1, "guard": 1, "medium": 1, "madman": 1}, {"evil": 1},
])
def test_invalid_settings_are_rejected(settings):
    with pytest.raises(HTTPException) as error:
        state.settings_for(settings)
    assert error.value.status_code == 400


def test_owner_controls_configuration_and_start_only_before_game():
    room = state.create_room({"user_id": "owner", "name": "作成者"}, "ルーム", {})
    state.join_room(room, {"user_id": "other", "name": "参加者"})
    for action in ("configure", "start"):
        with pytest.raises(HTTPException) as error:
            state.command(room, "other", {"action": action, "settings": {}})
        assert error.value.status_code == 403
    with pytest.raises(HTTPException) as error:
        state.command(room, "owner", {"action": "start"})
    assert error.value.status_code == 400
    for index in range(2):
        state.join_room(room, {"user_id": f"new{index}", "name": f"新規{index}"})
    state.command(room, "owner", {"action": "configure", "settings": {"night_seconds": 15, "medium": 1, "guard": 0}})
    state.command(room, "owner", {"action": "start"})
    assert sum(player.role == "medium" for player in room.players.values()) == 1
    with pytest.raises(HTTPException) as error:
        state.command(room, "owner", {"action": "configure", "settings": {}})
    assert error.value.status_code == 409


def test_roles_and_private_results_are_not_visible_to_other_players_or_owner():
    room, users = game(["wolf", "seer", "guard", "villager"])
    state.private_record(room, "u_1", "秘密の占い結果")
    room.wolf_chat.append({"name": "人狼", "text": "秘密の相談"})
    for user in users:
        view = state.view(room, user["user_id"])
        assert all("role" not in player for player in view["players"])
        assert view["you"]["role"] == room.players[user["user_id"]].role
        assert ("秘密の占い結果" in view["private_log"]) == (user["user_id"] == "u_1")
        assert bool(view["wolf_chat"]) == (user["user_id"] == "u_0")
    state.finish(room, "village")
    assert all("role" in player for player in state.view(room, "u_0")["players"])


def test_wolves_know_only_their_teammates_and_madman_cannot_read_wolf_chat():
    room, _ = game(["wolf", "wolf", "madman", "seer", "guard", "villager"])
    room.wolf_chat.append({"name": "参加者0", "text": "相談"})
    assert state.view(room, "u_0")["you"]["teammates"] == [{"user_id": "u_1", "name": "参加者1"}]
    madman = state.view(room, "u_2")
    assert madman["you"]["team"] == "wolves"
    assert not madman["you"]["teammates"]
    assert not madman["wolf_chat"]


def test_guard_blocks_attack_and_seer_result_is_delivered_at_dawn():
    room, _ = game(["wolf", "seer", "guard", "villager"])
    for user_id, target in (("u_0", "u_3"), ("u_1", "u_0"), ("u_2", "u_3")):
        state.command(room, user_id, {"action": "night", "target": target}, now=101)
    assert not state.view(room, "u_1")["private_log"]
    assert not state.advance(room, 159)
    assert state.advance(room, 160)
    assert all(player.alive for player in room.players.values())
    assert room.phase == "discussion"
    assert "参加者0 は人狼です" in state.view(room, "u_1")["private_log"][0]
    assert not state.view(room, "u_2")["private_log"]
    assert not room.actions


def test_seer_can_change_target_without_getting_multiple_results_and_madman_is_white():
    room, _ = game(["wolf", "seer", "guard", "madman"])
    state.command(room, "u_1", {"action": "night", "target": "u_0"}, now=101)
    state.command(room, "u_1", {"action": "night", "target": "u_3"}, now=102)
    state.command(room, "u_0", {"action": "night", "target": "u_2"}, now=102)
    state.advance(room, 160)
    assert room.players["u_2"].alive is False
    results = state.view(room, "u_1")["private_log"]
    assert len(results) == 1 and "参加者3 は人狼ではありません" in results[0]


def test_phases_advance_with_no_client_actions_and_attack_defaults_on_timeout():
    room, _ = game(["wolf", "seer", "guard", "villager"])
    state.advance(room, 160)
    assert sum(player.alive for player in room.players.values()) == 3
    assert room.phase == "discussion"
    state.advance(room, room.deadline)
    assert room.phase == "vote"
    state.advance(room, room.deadline)
    assert room.phase == "night" and room.day == 2
    state.advance(room, room.deadline)
    assert room.phase == "finished" and room.winner == "wolves"


def test_vote_ends_when_all_living_players_vote_and_village_wins():
    room, _ = game(["wolf", "seer", "guard", "villager"])
    state.command(room, "u_0", {"action": "night", "target": "u_3"}, now=101)
    state.command(room, "u_2", {"action": "night", "target": "u_3"}, now=101)
    state.advance(room, 160)
    state.advance(room, room.deadline)
    for user_id, target in (("u_0", "u_1"), ("u_1", "u_0"), ("u_2", "u_0"), ("u_3", "u_0")):
        state.command(room, user_id, {"action": "vote", "target": target}, now=341)
    assert room.phase == "finished"
    assert room.winner == "village"
    assert not room.players["u_0"].alive


def test_tied_vote_does_not_execute_anyone():
    room, _ = game(["wolf", "seer", "guard", "villager"])
    state.begin_phase(room, "vote", 200)
    for user_id, target in (("u_0", "u_1"), ("u_1", "u_0"), ("u_2", "u_0"), ("u_3", "u_1")):
        state.command(room, user_id, {"action": "vote", "target": target}, now=201)
    assert all(player.alive for player in room.players.values())
    assert room.phase == "night"
    assert "同票" in room.log[-2]


def test_medium_receives_execution_result_privately():
    room, _ = game(["wolf", "wolf", "medium", "seer", "guard", "madman", "villager"])
    state.begin_phase(room, "vote", 200)
    state.command(room, "u_2", {"action": "vote", "target": "u_0"}, now=201)
    state.advance(room, 260)
    assert room.phase == "night"
    assert "参加者0 は人狼でした" in state.view(room, "u_2")["private_log"][0]
    assert not state.view(room, "u_1")["private_log"]


@pytest.mark.parametrize("user_id,target", [("u_0", "u_0"), ("u_2", "u_2"), ("u_3", "u_3"), ("u_1", "unknown")])
def test_invalid_night_actions_do_not_change_state(user_id, target):
    room, _ = game(["wolf", "seer", "guard", "villager"])
    with pytest.raises(HTTPException):
        state.command(room, user_id, {"action": "night", "target": target}, now=101)
    assert not room.actions


def test_dead_players_cannot_act_vote_or_chat():
    room, _ = game(["wolf", "seer", "guard", "villager"])
    room.players["u_1"].alive = False
    for phase, command in (("night", {"action": "night", "target": "u_0"}),
                           ("vote", {"action": "vote", "target": "u_0"}),
                           ("discussion", {"action": "chat", "text": "発言"})):
        state.begin_phase(room, phase, 200)
        with pytest.raises(HTTPException) as error:
            state.command(room, "u_1", command, now=201)
        assert error.value.status_code == 403
        assert not state.view(room, "u_1")["candidates"]


def test_chat_channels_enforce_role_and_phase_and_reject_spoofed_identity():
    room, _ = game(["wolf", "seer", "guard", "villager"])
    state.command(room, "u_0", {"action": "chat", "channel": "wolves", "text": "相談", "name": "偽名"})
    assert room.wolf_chat == [{"name": "参加者0", "text": "相談"}]
    for user_id, channel in (("u_1", "wolves"), ("u_0", "public")):
        with pytest.raises(HTTPException):
            state.command(room, user_id, {"action": "chat", "channel": channel, "text": "発言"})
    state.begin_phase(room, "discussion", 200)
    state.command(room, "u_1", {"action": "chat", "text": "発言"})
    assert room.chat[0]["name"] == "参加者1"


def test_madman_counts_as_non_wolf_for_parity_and_wins_with_wolves():
    room, _ = game(["wolf", "madman", "villager", "seer"])
    assert not state.check_winner(room)
    room.players["u_2"].alive = room.players["u_3"].alive = False
    assert state.check_winner(room)
    assert room.winner == "wolves"


def test_reset_clears_all_private_information_for_next_game():
    room, _ = game(["wolf", "seer", "guard", "villager"])
    state.private_record(room, "u_1", "占い結果")
    state.finish(room, "village")
    state.command(room, "u_0", {"action": "reset"})
    assert room.phase == "lobby"
    assert not room.private and not room.actions and not room.votes
    assert all(player.role is None and player.alive for player in room.players.values())
    assert all("role" not in player for player in state.view(room, "u_1")["players"])


def test_owner_leave_transfers_control_and_last_leave_removes_room():
    room = state.create_room({"user_id": "a", "name": "A"}, "部屋", {})
    state.join_room(room, {"user_id": "b", "name": "B"})
    state.leave_room(room, "a")
    assert room.owner_id == "b"
    state.leave_room(room, "b")
    assert room.code not in state.rooms


def test_started_room_cannot_be_joined_or_left():
    room, _ = game()
    with pytest.raises(HTTPException) as error:
        state.join_room(room, {"user_id": "new", "name": "新規"})
    assert error.value.status_code == 409
    with pytest.raises(HTTPException) as error:
        state.leave_room(room, "u_0")
    assert error.value.status_code == 409


def test_draw_after_thirty_days():
    room, _ = game()
    room.day = 30
    state.begin_phase(room, "vote", 200)
    state.advance(room, 260)
    assert room.winner == "draw" and room.phase == "finished"


def agree(room, user_id, agreed=True, now=101):
    state.command(room, user_id, {"action": "skip", "phase_id": room.phase_id, "agreed": agreed}, now=now)


def test_every_role_selects_a_private_night_target_before_agreeing_to_skip():
    room, _ = game(["wolf", "seer", "guard", "medium", "madman", "villager"])
    phase_id = room.phase_id
    targets = ["u_5", "u_0", "u_5", "u_0", "u_0", "u_0"]
    for user_id, target in zip(room.players, targets):
        with pytest.raises(HTTPException) as error:
            agree(room, user_id)
        assert error.value.status_code == 409
        state.command(room, user_id, {"action": "night", "phase_id": phase_id, "target": target}, now=101)
        own_view = state.view(room, user_id)
        assert own_view["you"]["selection"] == target
        assert own_view["candidates"]
        for other_id in room.players:
            other_view = state.view(room, other_id)
            assert other_view["you"]["selection"] == room.actions.get(other_id)
            assert all(set(player) == {"user_id", "name", "alive", "voted"} for player in other_view["players"])
            assert "actions" not in other_view and "night_ready" not in other_view
    assert room.phase == "night"  # 対象を選んだだけでは夜を終えない。
    for user_id in list(room.players)[:-1]:
        agree(room, user_id)
        assert room.phase == "night"
        assert not state.view(room, "u_1")["private_log"]
    agree(room, "u_5", now=110)
    assert room.phase == "discussion" and room.deadline == 290
    assert room.phase_id > phase_id and not room.skip_agreements
    assert all(player.alive for player in room.players.values())
    assert len(room.private) == 1 and "参加者0 は人狼です" in room.private["u_1"][0]


def test_passive_night_choices_do_not_attack_protect_or_reveal_results():
    room, _ = game(["wolf", "medium", "madman", "villager"])
    for user_id, target in (("u_0", "u_3"), ("u_1", "u_3"), ("u_2", "u_1"), ("u_3", "u_2")):
        state.command(room, user_id, {"action": "night", "target": target}, now=101)
        agree(room, user_id)
    assert not room.players["u_3"].alive
    assert all(room.players[user_id].alive for user_id in ("u_0", "u_1", "u_2"))
    assert room.phase == "discussion" and not room.private


def test_wolves_still_cannot_target_their_teammates_at_night():
    room, _ = game(["wolf", "wolf", "seer", "guard", "medium", "villager"])
    with pytest.raises(HTTPException) as error:
        state.command(room, "u_0", {"action": "night", "target": "u_1"}, now=101)
    assert error.value.status_code == 400 and not room.actions


def test_discussion_skip_needs_unanimity_and_allows_withdrawal_and_repeated_agreement():
    room, _ = game()
    state.begin_phase(room, "discussion", 200)
    phase_id = room.phase_id
    agree(room, "u_0", now=201)
    agree(room, "u_0", now=202)
    assert state.view(room, "u_0")["skip"] == {"agreed": 1, "required": 4, "you_agreed": True}
    assert state.view(room, "u_1")["skip"]["you_agreed"] is False
    agree(room, "u_0", False, now=203)
    assert not room.skip_agreements
    for user_id in ("u_1", "u_2", "u_3"):
        agree(room, user_id, now=204)
    assert room.phase == "discussion" and room.deadline == 380
    agree(room, "u_0", now=205)
    assert room.phase == "vote" and room.deadline == 265
    assert room.phase_id > phase_id and not room.skip_agreements
    assert "全員がスキップ" in room.log[-2]


@pytest.mark.parametrize("ballots", [{}, {"u_1": "u_0", "u_2": "u_1"}, {"u_1": "u_0"}])
def test_vote_skip_uses_current_ballots_and_timeout_rules(ballots):
    room, _ = game(["wolf", "medium", "guard", "villager"])
    state.begin_phase(room, "vote", 200)
    for user_id, target in ballots.items():
        state.command(room, user_id, {"action": "vote", "target": target}, now=201)
    for user_id in room.players:
        agree(room, user_id, now=202)
    assert not room.skip_agreements
    if len(ballots) == 1:
        assert room.phase == "finished" and room.winner == "village"
        assert not room.players["u_0"].alive
        assert "参加者0 は人狼でした" in room.private["u_1"][0]
    else:
        assert room.phase == "night" and room.day == 2
        assert room.deadline == 262
        assert all(player.alive for player in room.players.values())


def test_dead_players_are_excluded_from_skip_consensus_and_cannot_agree():
    room, _ = game(["wolf", "seer", "guard", "villager"])
    room.players["u_3"].alive = False
    state.begin_phase(room, "discussion", 200)
    assert state.view(room, "u_3")["skip"]["required"] == 3
    with pytest.raises(HTTPException) as error:
        agree(room, "u_3", now=201)
    assert error.value.status_code == 403
    for user_id in ("u_0", "u_1", "u_2"):
        agree(room, user_id, now=202)
    assert room.phase == "vote"


@pytest.mark.parametrize("value", [None, 1, "true", [], {}])
def test_invalid_skip_agreement_does_not_change_state(value):
    room, _ = game()
    state.begin_phase(room, "discussion", 200)
    with pytest.raises(HTTPException) as error:
        state.command(room, "u_0", {"action": "skip", "phase_id": room.phase_id, "agreed": value}, now=201)
    assert error.value.status_code == 400 and not room.skip_agreements


@pytest.mark.parametrize("phase_id", [None, True, "1", 1.0])
def test_skip_requires_an_integer_phase_id(phase_id):
    room, _ = game()
    with pytest.raises(HTTPException) as error:
        state.command(room, "u_0", {"action": "skip", "phase_id": phase_id, "agreed": True}, now=101)
    assert error.value.status_code == 400 and not room.skip_agreements


@pytest.mark.parametrize("action", ["skip", "night", "vote"])
def test_commands_from_an_earlier_phase_are_rejected_even_when_the_phase_name_repeats(action):
    room, _ = game()
    state.begin_phase(room, "vote" if action == "vote" else "night", 200)
    old_phase_id = room.phase_id
    state.begin_phase(room, "discussion", 300)
    state.begin_phase(room, "vote" if action == "vote" else "night", 400)
    with pytest.raises(HTTPException) as error:
        state.command(room, "u_0", {"action": action, "phase_id": old_phase_id, "agreed": True, "target": "u_1"}, now=401)
    assert error.value.status_code == 409
    assert not room.actions and not room.votes and not room.skip_agreements


def test_skip_is_unavailable_outside_game_and_agreements_do_not_survive_reset():
    room, _ = game()
    state.begin_phase(room, "discussion", 200)
    agree(room, "u_0", now=201)
    old_phase_id = room.phase_id
    state.finish(room, "village")
    assert not room.skip_agreements
    with pytest.raises(HTTPException) as error:
        agree(room, "u_0", now=202)
    assert error.value.status_code == 409
    state.command(room, "u_0", {"action": "reset"}, now=203)
    with pytest.raises(HTTPException) as error:
        agree(room, "u_0", now=204)
    assert error.value.status_code == 409
    state.command(room, "u_0", {"action": "start"}, now=205)
    assert room.phase_id > old_phase_id and not room.skip_agreements
