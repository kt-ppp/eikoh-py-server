from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import pages
from app.apps.sns import routes as sns

APP_DIR = Path(__file__).resolve().parent
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

# サイト共通のページと、アプリごとの画面を登録する。
app.include_router(pages.router)
app.include_router(sns.router)

app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
app.mount("/app/sns/static", StaticFiles(directory=sns.APP_DIR / "static"), name="sns_static")
