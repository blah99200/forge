"""Vendor template management routes."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.template import VendorTemplate, FieldMapping, ExtractionRule

router = APIRouter()


class FieldMappingCreate(BaseModel):
    field_name: str
    field_type: str = "header"
    region: dict
    extraction_method: str = "text_region"
    post_processing: dict = {}


class TemplateCreate(BaseModel):
    name: str
    variant: str = "standard"
    vendor_tokens: list[str] = []
    field_mappings: list[FieldMappingCreate] = []
    auto_push_enabled: bool = False
    confidence_threshold: float = 0.9


@router.get("")
async def list_templates(db: Session = Depends(get_db)):
    """List all vendor templates."""
    templates = db.query(VendorTemplate).order_by(VendorTemplate.name).all()
    return [
        {
            "id": t.id,
            "name": t.name,
            "variant": t.variant,
            "vendor_tokens": t.get_vendor_tokens(),
            "field_count": len(t.field_mappings),
            "rule_count": len(t.extraction_rules),
            "auto_push_enabled": t.auto_push_enabled,
            "version": t.version,
        }
        for t in templates
    ]


@router.post("")
async def create_template(data: TemplateCreate, db: Session = Depends(get_db)):
    """Create a new vendor template with field mappings."""
    import json

    template = VendorTemplate(
        name=data.name,
        variant=data.variant,
        auto_push_enabled=data.auto_push_enabled,
        confidence_threshold=data.confidence_threshold,
    )
    template.set_vendor_tokens(data.vendor_tokens)

    for fm in data.field_mappings:
        mapping = FieldMapping(
            field_name=fm.field_name,
            field_type=fm.field_type,
            region=json.dumps(fm.region),
            extraction_method=fm.extraction_method,
            post_processing=json.dumps(fm.post_processing),
        )
        template.field_mappings.append(mapping)

    db.add(template)
    db.commit()

    return {"id": template.id, "name": template.name, "field_count": len(template.field_mappings)}


@router.get("/{template_id}")
async def get_template(template_id: int, db: Session = Depends(get_db)):
    """Get template with all field mappings and rules."""
    template = db.get(VendorTemplate, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    return {
        "id": template.id,
        "name": template.name,
        "variant": template.variant,
        "vendor_tokens": template.get_vendor_tokens(),
        "anchor_positions": template.get_anchor_positions(),
        "layout_grid": template.layout_grid,
        "font_set": template.get_font_set(),
        "auto_push_enabled": template.auto_push_enabled,
        "confidence_threshold": template.confidence_threshold,
        "version": template.version,
        "field_mappings": [
            {
                "id": fm.id,
                "field_name": fm.field_name,
                "field_type": fm.field_type,
                "region": fm.get_region(),
                "extraction_method": fm.extraction_method,
                "post_processing": fm.get_post_processing(),
            }
            for fm in template.field_mappings
        ],
        "extraction_rules": [
            {
                "id": r.id,
                "rule_type": r.rule_type,
                "source_field": r.source_field,
                "target_field": r.target_field,
                "rule_config": r.get_rule_config(),
            }
            for r in template.extraction_rules
        ],
    }


@router.put("/{template_id}")
async def update_template(template_id: int, data: TemplateCreate, db: Session = Depends(get_db)):
    """Update an existing template."""
    import json

    template = db.get(VendorTemplate, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    template.name = data.name
    template.variant = data.variant
    template.set_vendor_tokens(data.vendor_tokens)
    template.auto_push_enabled = data.auto_push_enabled
    template.confidence_threshold = data.confidence_threshold
    template.version += 1

    # Replace field mappings
    template.field_mappings.clear()
    for fm in data.field_mappings:
        mapping = FieldMapping(
            field_name=fm.field_name,
            field_type=fm.field_type,
            region=json.dumps(fm.region),
            extraction_method=fm.extraction_method,
            post_processing=json.dumps(fm.post_processing),
        )
        template.field_mappings.append(mapping)

    db.commit()

    return {"id": template.id, "name": template.name, "version": template.version}


@router.post("/{template_id}/test")
async def test_template(template_id: int, db: Session = Depends(get_db)):
    """Test a template against a sample PDF. (Stub — needs file upload.)"""
    template = db.get(VendorTemplate, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    # TODO: Accept PDF upload and run extraction with this template
    return {"message": "Template test endpoint — not yet implemented"}
