from pathlib import Path

import markdown
import pytest
from fastapi.testclient import TestClient

from app import main


@pytest.fixture(autouse=True)
def clear_memory():
    main.sessions.clear()
    main.posts.clear()
    yield
    main.sessions.clear()
    main.posts.clear()


@pytest.fixture
def client():
    with TestClient(main.app) as browser:
        yield browser


def join(client, name="山田"):
    return client.post("/app/sns/join", data={"name": name}, follow_redirects=False)


def test_sns_displays_join_form(client):
    response = client.get("/app/sns/")
    assert response.status_code == 200
    assert "SNS Mock" in response.text
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
    token = client.cookies.get(main.SESSION_COOKIE)
    assert len(token) >= 40
    user_id = main.sessions[token]["user_id"]
    expires_at = main.sessions[token]["expires_at"]
    for _ in range(2):
        assert "山田 として参加中" in client.get("/app/sns/").text
    # 参加済みのブラウザは、再送信でも別のユーザーを作らない。
    assert join(client, "田中").status_code == 303
    assert client.cookies.get(main.SESSION_COOKIE) == token
    assert main.sessions[token]["user_id"] == user_id
    assert main.sessions[token]["expires_at"] == expires_at
    assert len(main.sessions) == 1


def test_secure_cookie_and_custom_ttl(client, monkeypatch):
    monkeypatch.setattr(main, "COOKIE_SECURE", True)
    monkeypatch.setattr(main, "LESSON_SESSION_TTL_SECONDS", 60)
    monkeypatch.setattr(main.time, "time", lambda: 1000)
    response = join(client)
    assert "Secure" in response.headers["set-cookie"]
    assert "Max-Age=60" in response.headers["set-cookie"]
    assert next(iter(main.sessions.values()))["expires_at"] == 1060


def test_post_redirect_list_and_reload(client):
    join(client)
    response = client.post("/app/sns/post", data={"text": "こんにちは\nみなさん"}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/app/sns/"
    for _ in range(2):
        page = client.get(response.headers["location"])
        assert "こんにちは\nみなさん" in page.text
        assert "山田" in page.text
    assert len(main.posts) == 1
    assert main.posts[0]["user_id"] == main.sessions[client.cookies.get(main.SESSION_COOKIE)]["user_id"]


def test_two_users_share_posts_with_separate_ids(client):
    join(client, "山田")
    client.post("/app/sns/post", data={"text": "Aからの投稿"})
    with TestClient(main.app) as other:
        join(other, "山田")
        assert other.cookies.get(main.SESSION_COOKIE) != client.cookies.get(main.SESSION_COOKIE)
        assert "Aからの投稿" in other.get("/app/sns/").text
        other.post("/app/sns/post", data={"text": "Bからの投稿"})
    assert "Bからの投稿" in client.get("/app/sns/").text
    assert main.posts[0]["user_id"] != main.posts[1]["user_id"]


@pytest.mark.parametrize("name", ["", " ", "\n\t　", "名" * 31])
def test_invalid_name_rejected(client, name):
    response = join(client, name)
    assert response.status_code == 400
    assert "名前は1〜30文字" in response.text
    assert not main.sessions
    assert main.SESSION_COOKIE not in client.cookies


@pytest.mark.parametrize("name", ["名", "名" * 30, "  山田  "])
def test_valid_name_boundaries(client, name):
    assert join(client, name).status_code == 303
    assert next(iter(main.sessions.values()))["name"] == name.strip()


@pytest.mark.parametrize("text", ["", " ", "\n\t　", "あ" * 501])
def test_invalid_post_rejected(client, text):
    join(client)
    response = client.post("/app/sns/post", data={"text": text})
    assert response.status_code == 400
    assert "投稿は1〜500文字" in response.text
    assert not main.posts


@pytest.mark.parametrize("text", ["あ", "あ" * 500, "  こんにちは  "])
def test_valid_post_boundaries(client, text):
    join(client)
    assert client.post("/app/sns/post", data={"text": text}, follow_redirects=False).status_code == 303
    assert main.posts[0]["text"] == text.strip()


@pytest.mark.parametrize("token", [None, "unknown-token"])
def test_unjoined_or_unknown_token_cannot_post(client, token):
    if token:
        client.cookies.set(main.SESSION_COOKIE, token, domain="testserver.local", path="/")
    response = client.post("/app/sns/post", data={"text": "投稿"})
    assert response.status_code == 200
    assert 'action="/app/sns/join"' in response.text
    assert main.SESSION_COOKIE not in client.cookies
    assert not main.posts


def test_expired_session_must_rejoin(client, monkeypatch):
    monkeypatch.setattr(main.time, "time", lambda: 1000)
    join(client)
    old_token = client.cookies.get(main.SESSION_COOKIE)
    old_id = main.sessions[old_token]["user_id"]
    # 期限の直前には同じ人として扱い、期限ちょうどで失効する。
    expires_at = main.sessions[old_token]["expires_at"]
    monkeypatch.setattr(main.time, "time", lambda: expires_at - 1)
    assert "山田 として参加中" in client.get("/app/sns/").text
    monkeypatch.setattr(main.time, "time", lambda: expires_at)
    assert 'action="/app/sns/join"' in client.get("/app/sns/").text
    assert old_token not in main.sessions
    assert 'action="/app/sns/join"' in client.post("/app/sns/post", data={"text": "期限切れ"}).text
    assert not main.posts
    join(client)
    assert main.sessions[client.cookies.get(main.SESSION_COOKIE)]["user_id"] != old_id


def test_lost_server_memory_requires_rejoin(client):
    join(client)
    client.post("/app/sns/post", data={"text": "以前の授業"})
    # 再起動でモジュールのメモリが失われた場合と同じ状態。
    main.sessions.clear()
    main.posts.clear()
    response = client.get("/app/sns/")
    assert 'action="/app/sns/join"' in response.text
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
    assert len(main.posts) == 100
    assert main.posts[0]["text"] == "投稿1"
    assert main.posts[-1]["text"] == "投稿100"
    assert len({post["id"] for post in main.posts}) == 100


def test_about_renders_student_markdown(client):
    response = client.get("/about")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    source = (Path(__file__).resolve().parents[1] / "docs" / "student.md").read_text(encoding="utf-8")
    assert markdown.markdown(source, extensions=["fenced_code"]) in response.text
    assert "<h1>このアプリについて</h1>" in response.text
    assert '<a href="/app/sns/">' in response.text


def test_stylesheet_is_served(client):
    response = client.get("/static/style.css")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")


@pytest.mark.parametrize("path", ["/api/messages", "/api/events", "/api/join", "/api/me", "/docs", "/redoc", "/openapi.json"])
def test_no_external_api(client, path):
    assert client.get(path).status_code == 404
