import pytest
from fastapi.testclient import TestClient

from app import sessions as session_store
from app.apps.sns import state
from app.main import app


def join(client, name="山田"):
    return client.post("/app/sns/join", data={"name": name}, follow_redirects=False)


def test_sns_displays_join_form(client):
    response = client.get("/app/sns/")
    assert response.status_code == 200
    assert "SNS Mock" in response.text
    assert 'href="/app/sns/static/style.css"' in response.text
    assert 'action="/app/sns/join"' in response.text
    assert 'action="/app/sns/post"' not in response.text


def test_join_cookie_and_identity_survive_reload(client):
    response = join(client)
    assert response.status_code == 303
    assert response.headers["location"] == "/app/sns/"
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert "Max-Age=21600" in cookie
    assert "Secure" not in cookie
    token = client.cookies.get(session_store.SESSION_COOKIE)
    assert len(token) >= 40
    user_id = session_store.get_session(token)["user_id"]
    expires_at = session_store.get_session(token)["expires_at"]
    for _ in range(2):
        assert "山田 として参加中" in client.get("/app/sns/").text
    # 参加済みのブラウザは、再送信でも別のユーザーを作らない。
    assert join(client, "田中").status_code == 303
    assert client.cookies.get(session_store.SESSION_COOKIE) == token
    assert session_store.get_session(token)["user_id"] == user_id
    assert session_store.get_session(token)["expires_at"] == expires_at
    with session_store.connection() as database:
        assert database.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1


def test_secure_cookie_and_custom_ttl(client, monkeypatch):
    monkeypatch.setattr(session_store, "COOKIE_SECURE", True)
    monkeypatch.setattr(session_store, "LESSON_SESSION_TTL_SECONDS", 60)
    monkeypatch.setattr(session_store.time, "time", lambda: 1000)
    response = join(client)
    assert "Secure" in response.headers["set-cookie"]
    assert "Max-Age=60" in response.headers["set-cookie"]
    assert session_store.get_session(client.cookies.get(session_store.SESSION_COOKIE))["expires_at"] == 1060


def test_post_redirect_list_and_reload(client):
    join(client)
    response = client.post("/app/sns/post", data={"text": "こんにちは\nみなさん"}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/app/sns/"
    for _ in range(2):
        page = client.get(response.headers["location"])
        assert "こんにちは\nみなさん" in page.text
        assert "山田" in page.text
    assert len(state.posts) == 1
    assert state.posts[0]["user_id"] == session_store.get_session(client.cookies.get(session_store.SESSION_COOKIE))["user_id"]


def test_two_users_share_posts_with_separate_ids(client):
    join(client, "山田")
    client.post("/app/sns/post", data={"text": "Aからの投稿"})
    with TestClient(app) as other:
        join(other, "山田")
        assert other.cookies.get(session_store.SESSION_COOKIE) != client.cookies.get(session_store.SESSION_COOKIE)
        assert "Aからの投稿" in other.get("/app/sns/").text
        other.post("/app/sns/post", data={"text": "Bからの投稿"})
    assert "Bからの投稿" in client.get("/app/sns/").text
    assert state.posts[0]["user_id"] != state.posts[1]["user_id"]


@pytest.mark.parametrize("name", ["", " ", "\n\t　", "名" * 31])
def test_invalid_name_rejected(client, name):
    response = join(client, name)
    assert response.status_code == 400
    assert "名前は1〜30文字" in response.text
    assert not session_store.SESSION_DB_PATH.exists()
    assert session_store.SESSION_COOKIE not in client.cookies


@pytest.mark.parametrize("name", ["名", "名" * 30, "  山田  "])
def test_valid_name_boundaries(client, name):
    assert join(client, name).status_code == 303
    assert session_store.get_session(client.cookies.get(session_store.SESSION_COOKIE))["name"] == name.strip()


@pytest.mark.parametrize("text", ["", " ", "\n\t　", "あ" * 501])
def test_invalid_post_rejected(client, text):
    join(client)
    response = client.post("/app/sns/post", data={"text": text})
    assert response.status_code == 400
    assert "投稿は1〜500文字" in response.text
    assert not state.posts


@pytest.mark.parametrize("text", ["あ", "あ" * 500, "  こんにちは  "])
def test_valid_post_boundaries(client, text):
    join(client)
    assert client.post("/app/sns/post", data={"text": text}, follow_redirects=False).status_code == 303
    assert state.posts[0]["text"] == text.strip()


@pytest.mark.parametrize("token", [None, "unknown-token"])
def test_unjoined_or_unknown_token_cannot_post(client, token):
    if token:
        client.cookies.set(session_store.SESSION_COOKIE, token, domain="testserver.local", path="/")
    response = client.post("/app/sns/post", data={"text": "投稿"})
    assert response.status_code == 200
    assert 'action="/app/sns/join"' in response.text
    assert session_store.SESSION_COOKIE not in client.cookies
    assert not state.posts


def test_expired_session_must_rejoin(client, monkeypatch):
    monkeypatch.setattr(session_store.time, "time", lambda: 1000)
    join(client)
    old_token = client.cookies.get(session_store.SESSION_COOKIE)
    old_id = session_store.get_session(old_token)["user_id"]
    # 期限の直前には同じ人として扱い、期限ちょうどで失効する。
    expires_at = session_store.get_session(old_token)["expires_at"]
    monkeypatch.setattr(session_store.time, "time", lambda: expires_at - 1)
    assert "山田 として参加中" in client.get("/app/sns/").text
    monkeypatch.setattr(session_store.time, "time", lambda: expires_at)
    assert 'action="/app/sns/join"' in client.get("/app/sns/").text
    assert session_store.get_session(old_token) is None
    assert 'action="/app/sns/join"' in client.post("/app/sns/post", data={"text": "期限切れ"}).text
    assert not state.posts
    join(client)
    assert session_store.get_session(client.cookies.get(session_store.SESSION_COOKIE))["user_id"] != old_id


def test_lost_posts_keep_the_database_session(client):
    join(client)
    client.post("/app/sns/post", data={"text": "以前の授業"})
    # 投稿が消えても、SQLiteに保存した参加情報は残る。
    state.posts.clear()
    response = client.get("/app/sns/")
    assert "山田 として参加中" in response.text
    assert "以前の授業" not in response.text


def test_names_and_posts_are_html_escaped(client):
    join(client, '<script>alert(1)</script>')
    payload = '<script>alert(2)</script><img src=x onerror="alert(3)">'
    client.post("/app/sns/post", data={"text": payload})
    page = client.get("/app/sns/").text
    assert "<script>" not in page
    assert "<img" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "&lt;script&gt;alert(2)&lt;/script&gt;" in page
    # 検証エラー時に再表示する入力もescapeする。
    page = client.post("/app/sns/post", data={"text": payload + "a" * 501}).text
    assert "<script>" not in page
    assert "<img" not in page


def test_oldest_posts_removed_after_100(client):
    join(client)
    for number in range(101):
        response = client.post("/app/sns/post", data={"text": f"投稿{number}"}, follow_redirects=False)
        assert response.status_code == 303
    assert len(state.posts) == 100
    assert state.posts[0]["text"] == "投稿1"
    assert state.posts[-1]["text"] == "投稿100"
    assert len({post["id"] for post in state.posts}) == 100
