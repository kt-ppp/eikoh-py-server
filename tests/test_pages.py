from pathlib import Path

import markdown
import pytest


def test_about_renders_student_markdown(client):
    response = client.get("/about")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    source = (Path(__file__).resolve().parents[1] / "docs" / "student.md").read_text(encoding="utf-8")
    assert markdown.markdown(source, extensions=["fenced_code"]) in response.text
    assert "<h1>このアプリについて</h1>" in response.text
    assert '<a href="/app/sns/">' in response.text


@pytest.mark.parametrize("path", ["/static/style.css", "/app/sns/static/style.css"])
def test_stylesheet_is_served(client, path):
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")


@pytest.mark.parametrize("path", ["/api/messages", "/api/events", "/api/join", "/api/me", "/docs", "/redoc", "/openapi.json"])
def test_no_external_api(client, path):
    assert client.get(path).status_code == 404
