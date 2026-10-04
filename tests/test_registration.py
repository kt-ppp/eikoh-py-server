from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient

from app import sessions as session_store
from app.apps.sns import routes as sns


def test_another_app_can_share_the_same_user():
    # テスト用の別アプリを登録し、SNSでの参加情報を共通処理で確認する。
    example = APIRouter(prefix="/app/example")

    @example.get("/", response_class=PlainTextResponse)
    async def example_page(request: Request):
        user = session_store.current_user(request)
        return user["user_id"] if user else "未参加"

    server = FastAPI()
    server.include_router(sns.router)
    server.include_router(example)

    with TestClient(server) as browser:
        assert browser.get("/app/example/").text == "未参加"
        response = browser.post("/app/sns/join", data={"name": "山田"}, follow_redirects=False)
        assert response.status_code == 303
        token = browser.cookies.get(session_store.SESSION_COOKIE)
        user_id = session_store.sessions[token]["user_id"]
        assert browser.get("/app/example/").text == user_id
        assert "山田 として参加中" in browser.get("/app/sns/").text
