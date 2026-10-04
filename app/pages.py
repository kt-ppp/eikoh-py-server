from pathlib import Path

import markdown
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

APP_DIR = Path(__file__).resolve().parent
router = APIRouter()
templates = Jinja2Templates(directory=APP_DIR / "templates")


@router.get("/about", response_class=HTMLResponse)
async def about(request: Request):
    source = (APP_DIR.parent / "docs" / "student.md").read_text(encoding="utf-8")
    return templates.TemplateResponse(
        request=request,
        name="about.html",
        context={"content": markdown.markdown(source, extensions=["fenced_code"])},
    )
