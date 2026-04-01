"""App settings routes."""

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models.connection import AccountingConnection, ReferenceData
from app.models.template import VendorTemplate

router = APIRouter()
_templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


class SettingsUpdate(BaseModel):
    llm_provider: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None


@router.get("")
async def get_settings():
    """Get current app settings (keys masked)."""
    return {
        "mode": settings.mode,
        "llm_provider": settings.llm_provider,
        "llm_api_key": settings.llm_api_key[:4] + "****" if settings.llm_api_key else "",
        "llm_model": settings.llm_model,
    }


@router.put("")
async def update_settings(data: SettingsUpdate):
    """Update app settings (runtime only — persists via .env in local mode)."""
    if data.llm_provider is not None:
        settings.llm_provider = data.llm_provider
    if data.llm_api_key is not None:
        settings.llm_api_key = data.llm_api_key
    if data.llm_model is not None:
        settings.llm_model = data.llm_model

    return {"updated": True}


@router.get("/page", response_class=HTMLResponse)
async def settings_page(request: Request, db: Session = Depends(get_db)):
    """Render the settings page."""
    connections = db.query(AccountingConnection).all()
    vendor_templates = db.query(VendorTemplate).all()

    template_list = [
        {
            "id": t.id,
            "name": t.name,
            "field_count": len(t.field_mappings),
            "rule_count": len(t.extraction_rules),
            "auto_push_enabled": t.auto_push_enabled,
        }
        for t in vendor_templates
    ]

    # Reference data summary
    ref_data_summary = None
    if connections:
        ref_data_summary = {
            "vendors": db.query(ReferenceData).filter(ReferenceData.data_type == "vendor").count(),
            "accounts": db.query(ReferenceData).filter(ReferenceData.data_type == "account").count(),
            "items": db.query(ReferenceData).filter(ReferenceData.data_type == "item").count(),
            "tax_codes": db.query(ReferenceData).filter(ReferenceData.data_type == "tax_code").count(),
        }

    conn_list = [
        {
            "id": c.id,
            "system_type": c.system_type,
            "name": c.name,
            "last_sync_at": c.last_sync_at.isoformat() if c.last_sync_at else None,
        }
        for c in connections
    ]

    return _templates.TemplateResponse("settings.html", {
        "request": request,
        "settings": settings,
        "connections": conn_list,
        "templates": template_list,
        "ref_data_summary": ref_data_summary,
    })
