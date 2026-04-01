"""Invoice upload, review, and approval routes."""

import hashlib
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models.invoice import Batch, Invoice
from app.extraction.pipeline import process_invoice

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


@router.post("/upload")
async def upload_invoices(files: list[UploadFile] = File(...), db: Session = Depends(get_db)):
    """Upload one or more PDF invoices for processing."""
    batch = Batch(invoice_count=len(files))
    db.add(batch)
    db.flush()

    results = []
    for file in files:
        if not file.filename or not file.filename.lower().endswith(".pdf"):
            results.append({"filename": file.filename, "error": "Not a PDF file"})
            continue

        content = await file.read()
        file_hash = hashlib.sha256(content).hexdigest()

        save_path = Path(settings.upload_dir) / f"{file_hash}.pdf"
        with open(save_path, "wb") as f:
            f.write(content)

        invoice = Invoice(
            file_path=str(save_path),
            file_hash=file_hash,
            batch_id=batch.id,
            status="pending",
        )
        db.add(invoice)
        db.flush()

        # Run extraction pipeline
        try:
            result = process_invoice(invoice, db)
            results.append({"filename": file.filename, "invoice_id": invoice.id, "status": invoice.status, **result})
        except Exception as e:
            invoice.status = "failed"
            results.append({"filename": file.filename, "invoice_id": invoice.id, "status": "failed", "error": str(e)})

    batch.status = "completed"
    db.commit()

    return {"batch_id": batch.id, "results": results}


@router.get("")
async def list_invoices(status: str | None = None, db: Session = Depends(get_db)):
    """List invoices, optionally filtered by status."""
    query = db.query(Invoice)
    if status:
        query = query.filter(Invoice.status == status)
    invoices = query.order_by(Invoice.created_at.desc()).all()

    return [
        {
            "id": inv.id,
            "file_hash": inv.file_hash,
            "template_id": inv.template_id,
            "status": inv.status,
            "confidence_overall": inv.confidence_overall,
            "accounting_system": inv.accounting_system,
            "created_at": inv.created_at.isoformat(),
        }
        for inv in invoices
    ]


@router.get("/{invoice_id}")
async def get_invoice(invoice_id: int, db: Session = Depends(get_db)):
    """Get invoice details including extraction data."""
    invoice = db.get(Invoice, invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    return {
        "id": invoice.id,
        "file_path": invoice.file_path,
        "file_hash": invoice.file_hash,
        "template_id": invoice.template_id,
        "status": invoice.status,
        "raw_extraction": invoice.get_raw_extraction(),
        "final_payload": invoice.get_final_payload(),
        "confidence_overall": invoice.confidence_overall,
        "field_confidences": invoice.get_field_confidences(),
        "accounting_system": invoice.accounting_system,
        "external_id": invoice.external_id,
        "created_at": invoice.created_at.isoformat(),
    }


@router.put("/{invoice_id}/extraction")
async def update_extraction(invoice_id: int, extraction: dict, db: Session = Depends(get_db)):
    """Update extraction data (user corrections)."""
    invoice = db.get(Invoice, invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    invoice.set_raw_extraction(extraction)
    invoice.status = "reviewed"
    db.commit()

    return {"id": invoice.id, "status": invoice.status}


@router.post("/{invoice_id}/approve")
async def approve_invoice(invoice_id: int, db: Session = Depends(get_db)):
    """Approve invoice and push to accounting system."""
    invoice = db.get(Invoice, invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    if invoice.status not in ("extracted", "reviewed"):
        raise HTTPException(status_code=400, detail=f"Cannot approve invoice in '{invoice.status}' status")

    # TODO: Push to accounting system via adapter
    invoice.status = "approved"
    db.commit()

    return {"id": invoice.id, "status": invoice.status}


@router.post("/{invoice_id}/reject")
async def reject_invoice(invoice_id: int, db: Session = Depends(get_db)):
    """Reject an invoice."""
    invoice = db.get(Invoice, invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    invoice.status = "failed"
    db.commit()

    return {"id": invoice.id, "status": invoice.status}


# --- HTML page routes ---

@router.get("/upload", response_class=HTMLResponse)
async def upload_page(request: Request):
    """Render the upload page."""
    return templates.TemplateResponse("upload.html", {"request": request})


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request, db: Session = Depends(get_db)):
    """Render the invoice dashboard."""
    invoices = db.query(Invoice).order_by(Invoice.created_at.desc()).limit(100).all()

    # Count by status
    counts = {"total": len(invoices)}
    for status in ("pending", "classified", "extracted", "reviewed", "approved", "pushed", "failed"):
        counts[status] = sum(1 for inv in invoices if inv.status == status)

    # Enrich with payload data for display
    enriched = []
    for inv in invoices:
        payload = inv.get_final_payload()
        enriched.append({
            "id": inv.id,
            "vendor_name": payload.get("vendor", {}).get("name", ""),
            "invoice_number": payload.get("invoice_number", ""),
            "total": payload.get("total", ""),
            "confidence_overall": inv.confidence_overall,
            "status": inv.status,
        })

    return templates.TemplateResponse("batch_dashboard.html", {
        "request": request,
        "invoices": enriched,
        "counts": counts,
    })


@router.get("/{invoice_id}/review", response_class=HTMLResponse)
async def review_page(request: Request, invoice_id: int, db: Session = Depends(get_db)):
    """Render the invoice review page."""
    invoice = db.get(Invoice, invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    extraction = invoice.get_raw_extraction()
    field_confidences = invoice.get_field_confidences()

    # Build regions for PDF overlay
    regions = []
    if invoice.template_id:
        from app.models.template import VendorTemplate
        template = db.get(VendorTemplate, invoice.template_id)
        if template:
            for fm in template.field_mappings:
                region = fm.get_region()
                region["field"] = fm.field_name
                region["type"] = "mapped"
                regions.append(region)

    # If LLM extraction, regions come from the extraction data
    for field_name, field_data in extraction.items():
        if isinstance(field_data, dict) and "region" in field_data:
            region = field_data["region"]
            region["field"] = field_name
            region["type"] = "suggested"
            regions.append(region)

    return templates.TemplateResponse("invoice_review.html", {
        "request": request,
        "invoice": invoice,
        "extraction": extraction,
        "confidence": {
            "overall": invoice.confidence_overall or 0,
            "fields": field_confidences,
        },
        "regions": regions,
        "issues": invoice.get_final_payload().get("issues", []),
    })


@router.get("/{invoice_id}/map", response_class=HTMLResponse)
async def mapping_page(request: Request, invoice_id: int, db: Session = Depends(get_db)):
    """Render the mapping builder page for an unmapped invoice."""
    invoice = db.get(Invoice, invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    extraction = invoice.get_raw_extraction()

    # Build suggested fields and regions from LLM extraction
    suggested_fields = {}
    suggested_regions = []
    for field_name, field_data in extraction.items():
        if field_name == "line_items":
            continue
        if isinstance(field_data, dict):
            suggested_fields[field_name] = {
                "value": field_data.get("value", ""),
                "region": field_data.get("region"),
                "confidence": field_data.get("confidence", 0),
            }
            if field_data.get("region"):
                region = dict(field_data["region"])
                region["field"] = field_name
                region["type"] = "suggested"
                suggested_regions.append(region)
        else:
            suggested_fields[field_name] = {"value": field_data, "region": None, "confidence": 0}

    return templates.TemplateResponse("mapping_builder.html", {
        "request": request,
        "invoice_id": invoice_id,
        "suggested": extraction,
        "suggested_fields": suggested_fields,
        "suggested_regions": suggested_regions,
    })


@router.get("/{invoice_id}/pdf")
async def serve_pdf(invoice_id: int, db: Session = Depends(get_db)):
    """Serve the PDF file for an invoice."""
    invoice = db.get(Invoice, invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    pdf_path = Path(invoice.file_path)
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="PDF file not found")

    return FileResponse(str(pdf_path), media_type="application/pdf")
