"""Dashboard route — minimal HTMX/Alpine.js live metrics page."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.core.schemas import Urgency
from app.core.storage import get_insights, query_extractions

router = APIRouter(tags=["dashboard"])

_templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
_templates.env.filters["from_json"] = json.loads


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    """Main dashboard — sentiment breakdown, top complaints, recent urgent reviews."""
    insights = await get_insights()
    urgent_reviews = await query_extractions(urgency=Urgency.high, limit=10)
    recent_reviews = await query_extractions(limit=5)
    return _templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "insights": insights,
            "urgent_reviews": urgent_reviews,
            "recent_reviews": recent_reviews,
        },
    )
