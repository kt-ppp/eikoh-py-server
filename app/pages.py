from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

APP_DIR = Path(__file__).resolve().parent
router = APIRouter()
root_templates = Jinja2Templates(directory=APP_DIR.parent)


@router.get("/", response_class=HTMLResponse)
@router.get("/index.html", response_class=HTMLResponse)
async def index(request: Request):
    return root_templates.TemplateResponse(
        request=request, name="index.html",
        context={"apps": request.app.state.app_catalog},
    )
