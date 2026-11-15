import secrets
import time
from collections import Counter
from dataclasses import dataclass, field
from random import SystemRandom

from fastapi import HTTPException

ROLES = {
    "villager": {"name": "村人", "team": "village", "description": "特別な能力はありません。話し合いと投票で人狼を探します。"},
    "wolf": {"name": "人狼", "team": "wolves", "description": "夜に仲間と相談して1人を襲撃します。"},
    "seer": {"name": "占い師", "team": "village", "description": "夜に1人を占って、人狼かどうかを調べます。"},
    "guard": {"name": "騎士", "team": "village", "description": "夜に自分以外の1人を襲撃から守ります。同じ人を続けて守ることもできます。"},
    "medium": {"name": "霊媒師", "team": "village", "description": "昼に処刑された人が人狼だったかが分かります。"},
    "madman": {"name": "狂人", "team": "wolves", "description": "人狼陣営ですが、仲間が誰かは分かりません。占いや霊媒では人狼ではないと出ます。"},
}
PHASES = {"lobby": "参加者を募集中", "night": "夜", "discussion": "昼の議論", "vote": "投票", "finished": "ゲーム終了"}
DEFAULT_SETTINGS = {
    "capacity": 8, "wolves": 1, "seer": 1, "guard": 1, "medium": 0, "madman": 0,
    "night_seconds": 60, "discussion_seconds": 180, "vote_seconds": 60,
}
ROLE_SETTINGS = {"wolf": "wolves", "seer": "seer", "guard": "guard", "medium": "medium", "madman": "madman"}
LIMITS = {
    "capacity": (4, 16), "wolves": (1, 7), "seer": (0, 1), "guard": (0, 1),
    "medium": (0, 1), "madman": (0, 1), "night_seconds": (5, 600),
    "discussion_seconds": (10, 1800), "vote_seconds": (5, 600),
}
rooms: dict[str, "Room"] = {}
random = SystemRandom()


@dataclass
class Player:
    user_id: str
    name: str
    role: str | None = None
    alive: bool = True


@dataclass
class Room:
    code: str
    name: str
    owner_id: str
    settings: dict
    players: dict[str, Player] = field(default_factory=dict)
    phase: str = "lobby"
    phase_id: int = 0
    day: int = 0
    deadline: float | None = None
    winner: str | None = None
    actions: dict[str, str] = field(default_factory=dict)
    votes: dict[str, str] = field(default_factory=dict)
    skip_agreements: set[str] = field(default_factory=set)
    log: list[str] = field(default_factory=list)
    chat: list[dict] = field(default_factory=list)
    wolf_chat: list[dict] = field(default_factory=list)
    private: dict[str, list[str]] = field(default_factory=dict)
    version: int = 0
    updated_at: float = field(default_factory=time.time)


def fail(status: int, message: str):
    raise HTTPException(status, message)


def settings_for(values: dict, previous: dict | None = None) -> dict:
    if not isinstance(values, dict) or set(values) - set(LIMITS):
        fail(400, "設定の形式が不正です。")
    settings = {**(previous or DEFAULT_SETTINGS), **values}
    for key, (minimum, maximum) in LIMITS.items():
        value = settings[key]
        if type(value) is not int or not minimum <= value <= maximum:
            fail(400, f"{key} は{minimum}〜{maximum}の整数で指定してください。")
    if sum(settings[key] for key in ROLE_SETTINGS.values()) > settings["capacity"]:
        fail(400, "役職の人数が定員を超えています。")
    if settings["wolves"] * 2 >= settings["capacity"]:
        fail(400, "人狼は定員の半分未満にしてください。")
    return settings


def changed(room: Room):
    room.version += 1
    room.updated_at = time.time()


def record(room: Room, message: str):
    room.log.append(message)
    del room.log[:-100]


def private_record(room: Room, user_id: str, message: str):
    messages = room.private.setdefault(user_id, [])
    messages.append(message)
    del messages[:-100]


def get_room(code: str) -> Room:
    if not isinstance(code, str):
        fail(400, "ルームコードを入力してください。")
    room = rooms.get(code.strip().upper())
    if room is None:
        fail(404, "ルームが見つかりません。")
    return room


def member(room: Room, user_id: str) -> Player:
    player = room.players.get(user_id)
    if player is None:
        fail(403, "先にこのルームに参加してください。")
    return player


def owner(room: Room, user_id: str):
    member(room, user_id)
    if room.owner_id != user_id:
        fail(403, "この操作はルーム作成者だけが行えます。")


def create_room(user: dict, name: str, values: dict) -> Room:
    if len(rooms) >= 128:
        fail(409, "ルームがいっぱいです。しばらくしてから作成してください。")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 30:
        fail(400, "ルーム名は1〜30文字で入力してください。")
    settings = settings_for(values)
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    code = "".join(secrets.choice(alphabet) for _ in range(6))
    while code in rooms:
        code = "".join(secrets.choice(alphabet) for _ in range(6))
    room = Room(code, name.strip(), user["user_id"], settings)
    rooms[code] = room
    join_room(room, user)
    return room


def join_room(room: Room, user: dict):
    if user["user_id"] in room.players:
        return
    if room.phase != "lobby":
        fail(409, "開始済みのルームには参加できません。")
    if len(room.players) >= room.settings["capacity"]:
        fail(409, "このルームは満員です。")
    room.players[user["user_id"]] = Player(user["user_id"], user["name"])
    record(room, f"{user['name']} が参加しました。")
    changed(room)


def leave_room(room: Room, user_id: str):
    player = member(room, user_id)
    if room.phase not in ("lobby", "finished"):
        fail(409, "ゲーム中は退室できません。")
    del room.players[user_id]
    if not room.players:
        del rooms[room.code]
        return
    if room.owner_id == user_id:
        room.owner_id = next(iter(room.players))
        record(room, f"{room.players[room.owner_id].name} がルーム作成者を引き継ぎました。")
    record(room, f"{player.name} が退室しました。")
    changed(room)


def begin_phase(room: Room, phase: str, now: float):
    room.phase = phase
    room.phase_id += 1
    room.deadline = now + room.settings[f"{phase}_seconds"]
    room.actions.clear()
    room.votes.clear()
    room.skip_agreements.clear()


def start(room: Room, user_id: str, now: float):
    owner(room, user_id)
    if room.phase != "lobby":
        fail(409, "すでにゲームが始まっています。")
    count = len(room.players)
    if count < 4:
        fail(400, "開始には4人以上必要です。")
    roles = [role for role, key in ROLE_SETTINGS.items() for _ in range(room.settings[key])]
    if len(roles) > count:
        fail(400, "設定した役職の人数が参加者より多くなっています。")
    if room.settings["wolves"] * 2 >= count:
        fail(400, "開始時の人狼は参加者の半分未満にしてください。")
    roles += ["villager"] * (count - len(roles))
    random.shuffle(roles)
    room.private.clear()
    room.wolf_chat.clear()
    for player, role in zip(room.players.values(), roles):
        player.role = role
        player.alive = True
    room.day = 1
    room.winner = None
    begin_phase(room, "night", now)
    record(room, "ゲームを開始しました。1日目の夜です。")


def finish(room: Room, winner: str):
    room.phase = "finished"
    room.phase_id += 1
    room.skip_agreements.clear()
    room.deadline = None
    room.winner = winner
    message = {"village": "村人陣営の勝利です。", "wolves": "人狼陣営の勝利です。", "draw": "30日目を終えたため引き分けです。"}
    record(room, message[winner])


def check_winner(room: Room) -> bool:
    living = [player for player in room.players.values() if player.alive]
    wolves = sum(player.role == "wolf" for player in living)
    if wolves == 0:
        finish(room, "village")
        return True
    if wolves >= len(living) - wolves:
        finish(room, "wolves")
        return True
    return False


def resolve_night(room: Room, now: float):
    wolves = [player for player in room.players.values() if player.alive and player.role == "wolf"]
    targets = [player.user_id for player in room.players.values() if player.alive and player.role != "wolf"]
    ballots = Counter(room.actions[player.user_id] for player in wolves if player.user_id in room.actions)
    choices = [target for target, count in ballots.items() if count == max(ballots.values())] if ballots else targets
    target = random.choice(choices) if choices else None
    protected = {room.actions[player.user_id] for player in room.players.values()
                 if player.alive and player.role == "guard" and player.user_id in room.actions}
    for player in room.players.values():
        if player.alive and player.role == "seer" and player.user_id in room.actions:
            observed = room.players[room.actions[player.user_id]]
            result = "人狼です" if observed.role == "wolf" else "人狼ではありません"
            private_record(room, player.user_id, f"{room.day}日目の占い：{observed.name} は{result}。")
    if target and target not in protected:
        victim = room.players[target]
        victim.alive = False
        record(room, f"{room.day}日目の朝：{victim.name} が襲撃で亡くなりました。")
    else:
        record(room, f"{room.day}日目の朝：犠牲者はいませんでした。")
    if not check_winner(room):
        begin_phase(room, "discussion", now)


def resolve_vote(room: Room, now: float):
    ballots = Counter(room.votes.values())
    choices = [target for target, count in ballots.items() if count == max(ballots.values())] if ballots else []
    if len(choices) == 1:
        victim = room.players[choices[0]]
        victim.alive = False
        record(room, f"{room.day}日目の投票：{victim.name} が処刑されました。")
        result = "人狼でした" if victim.role == "wolf" else "人狼ではありませんでした"
        for player in room.players.values():
            if player.alive and player.role == "medium":
                private_record(room, player.user_id, f"{room.day}日目の霊媒：{victim.name} は{result}。")
    else:
        record(room, f"{room.day}日目の投票：同票または誰も投票しなかったため、処刑はありません。")
    if not check_winner(room):
        if room.day >= 30:
            finish(room, "draw")
        else:
            room.day += 1
            begin_phase(room, "night", now)
            record(room, f"{room.day}日目の夜になりました。")


def end_phase(room: Room, now: float):
    if room.phase == "night":
        resolve_night(room, now)
    elif room.phase == "discussion":
        begin_phase(room, "vote", now)
        record(room, f"{room.day}日目の投票を始めます。")
    elif room.phase == "vote":
        resolve_vote(room, now)


def advance(room: Room, now: float | None = None) -> bool:
    now = time.time() if now is None else now
    if room.deadline is None or now < room.deadline:
        return False
    end_phase(room, now)
    changed(room)
    return True


def candidates(room: Room, player: Player) -> list[Player]:
    if not player.alive:
        return []
    if room.phase == "vote":
        return [other for other in room.players.values() if other.alive and other.user_id != player.user_id]
    if room.phase == "night":
        return [other for other in room.players.values() if other.alive and other.user_id != player.user_id
                and not (player.role == "wolf" and other.role == "wolf")]
    return []


def command(room: Room, user_id: str, data: dict, now: float | None = None):
    now = time.time() if now is None else now
    player = member(room, user_id)
    if not isinstance(data, dict):
        fail(400, "操作の形式が不正です。")
    action = data.get("action")
    if action in ("night", "vote", "skip") and (action == "skip" or "phase_id" in data):
        if type(data.get("phase_id")) is not int:
            fail(400, "現在のフェーズを指定してください。")
        if data["phase_id"] != room.phase_id:
            fail(409, "フェーズが変わりました。もう一度操作してください。")
    if action == "configure":
        owner(room, user_id)
        if room.phase != "lobby":
            fail(409, "設定は開始前に変更してください。")
        settings = settings_for(data.get("settings"), room.settings)
        if settings["capacity"] < len(room.players):
            fail(400, "定員を参加者数より小さくできません。")
        room.settings = settings
        record(room, "ルームの設定を変更しました。")
    elif action == "start":
        start(room, user_id, now)
    elif action in ("night", "vote"):
        expected = "night" if action == "night" else "vote"
        if room.phase != expected:
            fail(409, "今はその操作を行えません。")
        if not player.alive:
            fail(403, "亡くなったプレイヤーは行動・投票できません。")
        target = data.get("target")
        if not isinstance(target, str) or target not in {other.user_id for other in candidates(room, player)}:
            fail(400, "選べるプレイヤーから対象を選んでください。")
        if action == "night":
            room.actions[user_id] = target
        else:
            room.votes[user_id] = target
            if all(other.user_id in room.votes for other in room.players.values() if other.alive):
                resolve_vote(room, now)
    elif action == "skip":
        if room.phase not in ("night", "discussion", "vote"):
            fail(409, "今は残り時間をスキップできません。")
        if not player.alive:
            fail(403, "生きている人だけスキップできます。")
        agreed = data.get("agreed")
        if type(agreed) is not bool:
            fail(400, "同意するかどうかを指定してください。")
        if agreed:
            if room.phase == "night" and user_id not in room.actions:
                fail(409, "夜の相手を選んでからスキップしてください。")
            room.skip_agreements.add(user_id)
        else:
            room.skip_agreements.discard(user_id)
        if all(other.user_id in room.skip_agreements for other in room.players.values() if other.alive):
            record(room, f"全員がスキップしたため、{PHASES[room.phase]}を終了しました。")
            end_phase(room, now)
    elif action == "chat":
        if not player.alive or room.phase == "finished":
            fail(403, "今は発言できません。")
        text = data.get("text")
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 500:
            fail(400, "発言は1〜500文字で入力してください。")
        channel = data.get("channel", "public")
        if channel == "wolves" and room.phase == "night" and player.role == "wolf":
            messages = room.wolf_chat
        elif channel == "public" and room.phase in ("lobby", "discussion", "vote"):
            messages = room.chat
        else:
            fail(403, "この時間・役職ではそのチャットを使えません。")
        messages.append({"name": player.name, "text": text.strip()})
        del messages[:-100]
    elif action == "reset":
        owner(room, user_id)
        if room.phase != "finished":
            fail(409, "ゲーム終了後に次のゲームを準備できます。")
        room.phase, room.day, room.deadline, room.winner = "lobby", 0, None, None
        room.phase_id += 1
        room.actions.clear()
        room.votes.clear()
        room.skip_agreements.clear()
        room.private.clear()
        room.wolf_chat.clear()
        room.chat.clear()
        for other in room.players.values():
            other.role, other.alive = None, True
        room.log.clear()
        record(room, "次のゲームを準備中です。")
    else:
        fail(400, "操作が不正です。")
    changed(room)


def view(room: Room, user_id: str) -> dict:
    player = member(room, user_id)
    role = ROLES.get(player.role)
    teammates = [{"user_id": other.user_id, "name": other.name} for other in room.players.values()
                 if other.role == "wolf" and other.user_id != user_id] if player.role == "wolf" else []
    return {
        "code": room.code, "name": room.name, "owner_id": room.owner_id,
        "phase": room.phase, "phase_id": room.phase_id, "phase_label": PHASES[room.phase], "day": room.day,
        "deadline": room.deadline, "winner": room.winner, "settings": room.settings.copy(),
        "skip": {"agreed": len(room.skip_agreements),
                 "required": sum(other.alive for other in room.players.values()),
                 "you_agreed": user_id in room.skip_agreements},
        "players": [{"user_id": other.user_id, "name": other.name, "alive": other.alive,
                     "voted": other.user_id in room.votes if room.phase == "vote" else False,
                     **({"role": ROLES[other.role]["name"]} if room.phase == "finished" and other.role else {})}
                    for other in room.players.values()],
        "you": {"user_id": user_id, "name": player.name, "alive": player.alive,
                "role": player.role, "role_name": role["name"] if role else None,
                "description": role["description"] if role else "ゲーム開始後に決まります。",
                "team": role["team"] if role else None, "teammates": teammates,
                "selection": (room.actions if room.phase == "night" else room.votes).get(user_id)},
        "candidates": [{"user_id": other.user_id, "name": other.name} for other in candidates(room, player)],
        "log": room.log.copy(), "chat": [message.copy() for message in room.chat],
        "wolf_chat": [message.copy() for message in room.wolf_chat] if player.role == "wolf" and player.alive else [],
        "private_log": room.private.get(user_id, []).copy(), "version": room.version,
    }
