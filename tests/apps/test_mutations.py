import pytest

from app.apps.sns import state


def join(client, name):
    client.cookies.clear()
    client.post("/app/sns/join", data={"name": name})
    return client.cookies.get("lesson_session")


def test_owner_can_edit_and_delete_using_forms(client):
    join(client, "山田")
    client.post("/app/sns/post", data={"text": "編集前"})
    post_id = state.posts[0]["id"]
    page = client.get("/app/sns/").text
    assert f'action="/app/sns/posts/{post_id}/edit"' in page
    assert f'action="/app/sns/posts/{post_id}/delete"' in page
    response = client.post(f"/app/sns/posts/{post_id}/edit", data={"text": "編集後"}, follow_redirects=False)
    assert response.status_code == 303
    assert state.posts[0]["text"] == "編集後"
    response = client.post(f"/app/sns/posts/{post_id}/delete", follow_redirects=False)
    assert response.status_code == 303
    assert not state.posts


@pytest.mark.parametrize("action", ["edit", "delete"])
def test_other_users_cannot_modify_posts_even_with_the_same_name(client, action):
    join(client, "山田")
    client.post("/app/sns/post", data={"text": "本人の投稿"})
    post_id = state.posts[0]["id"]
    owner_id = state.posts[0]["user_id"]
    join(client, "山田")
    page = client.get("/app/sns/").text
    assert f'/app/sns/posts/{post_id}/edit' not in page
    assert f'/app/sns/posts/{post_id}/delete' not in page
    response = client.post(f"/app/sns/posts/{post_id}/{action}", data={"text": "改ざん", "user_id": owner_id})
    assert response.status_code == 403
    assert state.posts[0]["text"] == "本人の投稿"


@pytest.mark.parametrize("action", ["edit", "delete"])
def test_unjoined_users_cannot_modify_posts(client, action):
    join(client, "山田")
    client.post("/app/sns/post", data={"text": "本人の投稿"})
    post_id = state.posts[0]["id"]
    client.cookies.clear()
    response = client.post(f"/app/sns/posts/{post_id}/{action}", data={"text": "改ざん"}, follow_redirects=False)
    assert response.status_code == 303
    assert state.posts[0]["text"] == "本人の投稿"


@pytest.mark.parametrize("text", ["", " \n　", "あ" * 501])
def test_invalid_edit_does_not_change_the_post(client, text):
    join(client, "山田")
    client.post("/app/sns/post", data={"text": "編集前"})
    post_id = state.posts[0]["id"]
    assert client.post(f"/app/sns/posts/{post_id}/edit", data={"text": text}).status_code == 400
    assert state.posts[0]["text"] == "編集前"


def test_edited_html_is_escaped(client):
    join(client, "山田")
    client.post("/app/sns/post", data={"text": "編集前"})
    post_id = state.posts[0]["id"]
    response = client.post(f"/app/sns/posts/{post_id}/edit", data={"text": '<script>alert(1)</script>'})
    assert '<script>alert(1)</script>' not in response.text
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in response.text


@pytest.mark.parametrize("action", ["edit", "delete"])
def test_missing_post_is_reported(client, action):
    join(client, "山田")
    assert client.post(f"/app/sns/posts/999999/{action}", data={"text": "test"}).status_code == 404


def test_deleted_post_ids_are_not_reused(client):
    join(client, "山田")
    client.post("/app/sns/post", data={"text": "最初の投稿"})
    old_id = state.posts[0]["id"]
    client.post(f"/app/sns/posts/{old_id}/delete")
    client.post("/app/sns/post", data={"text": "次の投稿"})
    assert state.posts[0]["id"] != old_id
    assert client.post(f"/app/sns/posts/{old_id}/edit", data={"text": "古い番号"}).status_code == 404


def test_expired_user_cannot_edit_or_delete(client, monkeypatch):
    from app import sessions as session_store

    token = join(client, "山田")
    client.post("/app/sns/post", data={"text": "本人の投稿"})
    post_id = state.posts[0]["id"]
    deadline = session_store.get_session(token)["expires_at"]
    monkeypatch.setattr(session_store.time, "time", lambda: deadline)
    for action in ("edit", "delete"):
        response = client.post(f"/app/sns/posts/{post_id}/{action}", data={"text": "変更"}, follow_redirects=False)
        assert response.status_code == 303
    assert state.posts[0]["text"] == "本人の投稿"
