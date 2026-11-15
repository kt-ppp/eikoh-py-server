from pathlib import Path

import pytest

from app import catalog
from app.main import application_modules


@pytest.mark.parametrize("url", ["/", "/index.html"])
def test_root_index_lists_every_registered_app(client, url):
    response = client.get(url, headers={"host": "hoge.hoge"})
    assert response.status_code == 200
    assert (Path(__file__).resolve().parents[1] / "index.html").is_file()
    assert "<h1>アプリ一覧</h1>" in response.text
    entries = catalog.app_entries(application_modules)
    assert {entry["url"] for entry in entries} == {"/app/sns/", "/app/werewolf/"}
    for entry in entries:
        assert f'href="{entry["url"]}"' in response.text
        assert entry["title"] in response.text


def test_about_page_is_removed(client):
    assert client.get("/about").status_code == 404
    assert 'href="/about"' not in client.get("/").text
    assert 'href="/about"' not in client.get("/app/sns/").text


@pytest.mark.parametrize("path, content_type", [
    ("/static/style.css", "text/css"),
    ("/app/sns/static/style.css", "text/css"),
    ("/app/sns/static/sns.js", "text/javascript"),
    ("/app/werewolf/static/style.css", "text/css"),
    ("/app/werewolf/static/room.js", "text/javascript"),
    ("/app/werewolf/static/rules.js", "text/javascript"),
])
def test_static_file_is_served(client, path, content_type):
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(content_type)


@pytest.mark.parametrize("path", ["/api/messages", "/api/events", "/api/join", "/api/me", "/docs", "/redoc", "/openapi.json"])
def test_no_external_api(client, path):
    assert client.get(path).status_code == 404
