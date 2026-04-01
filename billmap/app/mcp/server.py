"""BillMap MCP Server — exposes invoice extraction tools for AI agents.

Tools:
- extract_invoice: Process a PDF and extract AP bill data
- get_mappings: List available vendor mappings
- create_bill: Push an AP bill to an accounting system
- get_invoice_status: Check processing status
- list_pending_review: Get invoices awaiting human review

Run standalone: python -m app.mcp.server
"""

import json
import sys
from pathlib import Path

# MCP SDK — uses the official mcp package
try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, TextContent
    HAS_MCP = True
except ImportError:
    HAS_MCP = False


def create_mcp_server() -> "Server":
    """Create and configure the BillMap MCP server."""
    if not HAS_MCP:
        raise RuntimeError("MCP SDK not installed. Run: pip install mcp")

    server = Server("billmap")

    @server.list_tools()
    async def list_tools():
        return [
            Tool(
                name="extract_invoice",
                description="Extract AP bill data from a PDF invoice. Returns extracted fields, confidence scores, and suggested mapping regions.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "pdf_path": {
                            "type": "string",
                            "description": "Absolute path to the PDF invoice file",
                        },
                    },
                    "required": ["pdf_path"],
                },
            ),
            Tool(
                name="get_mappings",
                description="List all available vendor template mappings. Shows vendor name, field count, and whether auto-push is enabled.",
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="create_bill",
                description="Push an AP bill payload to an accounting system (QBO or Plexxis).",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "invoice_id": {
                            "type": "integer",
                            "description": "ID of the processed invoice to push",
                        },
                        "target_system": {
                            "type": "string",
                            "enum": ["qbo", "plexxis"],
                            "description": "Target accounting system",
                        },
                    },
                    "required": ["invoice_id", "target_system"],
                },
            ),
            Tool(
                name="get_invoice_status",
                description="Check the processing status and confidence of an invoice.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "invoice_id": {
                            "type": "integer",
                            "description": "Invoice ID to check",
                        },
                    },
                    "required": ["invoice_id"],
                },
            ),
            Tool(
                name="list_pending_review",
                description="Get invoices that are awaiting human review. These need user confirmation before being pushed to the accounting system.",
                inputSchema={"type": "object", "properties": {}},
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        # Lazy imports to avoid circular deps
        from app.db import SessionLocal
        from app.models.invoice import Invoice
        from app.models.template import VendorTemplate

        db = SessionLocal()
        try:
            if name == "extract_invoice":
                return await _handle_extract_invoice(db, arguments)
            elif name == "get_mappings":
                return await _handle_get_mappings(db)
            elif name == "create_bill":
                return await _handle_create_bill(db, arguments)
            elif name == "get_invoice_status":
                return await _handle_get_invoice_status(db, arguments)
            elif name == "list_pending_review":
                return await _handle_list_pending_review(db)
            else:
                return [TextContent(type="text", text=f"Unknown tool: {name}")]
        finally:
            db.close()

    return server


async def _handle_extract_invoice(db, arguments: dict):
    """Process a PDF and run the extraction pipeline."""
    import hashlib
    import shutil
    from mcp.types import TextContent
    from app.config import settings
    from app.models.invoice import Invoice
    from app.extraction.pipeline import process_invoice

    pdf_path = arguments.get("pdf_path", "")
    if not pdf_path or not Path(pdf_path).exists():
        return [TextContent(type="text", text=f"Error: PDF file not found: {pdf_path}")]

    # Copy to uploads and create invoice record
    content = Path(pdf_path).read_bytes()
    file_hash = hashlib.sha256(content).hexdigest()
    save_path = Path(settings.upload_dir) / f"{file_hash}.pdf"
    if not save_path.exists():
        shutil.copy2(pdf_path, save_path)

    invoice = Invoice(file_path=str(save_path), file_hash=file_hash, status="pending")
    db.add(invoice)
    db.flush()

    try:
        result = process_invoice(invoice, db)
        db.commit()
        return [TextContent(type="text", text=json.dumps({
            "invoice_id": invoice.id,
            "status": invoice.status,
            "confidence": invoice.confidence_overall,
            "extraction": result.get("extraction", {}),
            "payload": result.get("payload", {}),
            "routing": result.get("routing", {}),
        }, indent=2))]
    except Exception as e:
        db.rollback()
        return [TextContent(type="text", text=f"Extraction failed: {e}")]


async def _handle_get_mappings(db):
    from mcp.types import TextContent
    from app.models.template import VendorTemplate

    templates = db.query(VendorTemplate).all()
    result = [
        {
            "id": t.id,
            "vendor": t.name,
            "variant": t.variant,
            "field_count": len(t.field_mappings),
            "auto_push": t.auto_push_enabled,
        }
        for t in templates
    ]
    return [TextContent(type="text", text=json.dumps(result, indent=2))]


async def _handle_create_bill(db, arguments: dict):
    from mcp.types import TextContent
    from app.models.invoice import Invoice

    invoice_id = arguments.get("invoice_id")
    target_system = arguments.get("target_system", "qbo")

    invoice = db.get(Invoice, invoice_id)
    if not invoice:
        return [TextContent(type="text", text=f"Invoice {invoice_id} not found")]

    if invoice.status not in ("extracted", "reviewed", "approved"):
        return [TextContent(type="text", text=f"Invoice in '{invoice.status}' status — cannot push")]

    # TODO: Use actual adapter to push
    invoice.status = "approved"
    invoice.accounting_system = target_system
    db.commit()

    return [TextContent(type="text", text=json.dumps({
        "invoice_id": invoice.id,
        "status": "approved",
        "target_system": target_system,
        "message": "Bill push would happen here — adapter integration pending",
    }, indent=2))]


async def _handle_get_invoice_status(db, arguments: dict):
    from mcp.types import TextContent
    from app.models.invoice import Invoice

    invoice_id = arguments.get("invoice_id")
    invoice = db.get(Invoice, invoice_id)
    if not invoice:
        return [TextContent(type="text", text=f"Invoice {invoice_id} not found")]

    return [TextContent(type="text", text=json.dumps({
        "invoice_id": invoice.id,
        "status": invoice.status,
        "confidence": invoice.confidence_overall,
        "needs_review": invoice.status in ("extracted", "classified"),
        "template_id": invoice.template_id,
    }, indent=2))]


async def _handle_list_pending_review(db):
    from mcp.types import TextContent
    from app.models.invoice import Invoice

    invoices = db.query(Invoice).filter(
        Invoice.status.in_(["extracted", "classified"])
    ).order_by(Invoice.created_at.desc()).all()

    result = [
        {
            "invoice_id": inv.id,
            "status": inv.status,
            "confidence": inv.confidence_overall,
            "needs_mapping": inv.status == "classified",
        }
        for inv in invoices
    ]
    return [TextContent(type="text", text=json.dumps(result, indent=2))]


async def main():
    """Run the MCP server via stdio."""
    if not HAS_MCP:
        print("Error: MCP SDK not installed. Run: pip install mcp", file=sys.stderr)
        sys.exit(1)

    server = create_mcp_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
