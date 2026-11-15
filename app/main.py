import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import catalog, pages
from app.apps.werewolf import realtime as werewolf_realtime


@asynccontextmanager
async def lifespan(app: FastAPI):
    clock = asyncio.create_task(werewolf_realtime.run_clock())
    try:
        yield
    finally:
        clock.cancel()
        with suppress(asyncio.CancelledError):
            await clock

APP_DIR = Path(__file__).resolve().parent
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
application_modules = catalog.discover_apps()
app.state.app_catalog = catalog.app_entries(application_modules)

app.include_router(pages.router)
for module in application_modules:
    app.include_router(module.router)
    static_dir = module.APP_DIR / "static"
    if static_dir.is_dir():
        app.mount(module.router.prefix + "/static", StaticFiles(directory=static_dir),
                  name=module.__package__.rsplit(".", 1)[-1] + "_static")

app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
