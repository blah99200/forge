"""Accounting system connection and reference data routes."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.connection import AccountingConnection, ReferenceData

router = APIRouter()


class ConnectionCreate(BaseModel):
    system_type: str  # "qbo" | "plexxis"
    name: str = ""
    config: dict = {}


@router.get("")
async def list_connections(db: Session = Depends(get_db)):
    """List all accounting system connections."""
    connections = db.query(AccountingConnection).all()
    return [
        {
            "id": c.id,
            "system_type": c.system_type,
            "name": c.name,
            "last_sync_at": c.last_sync_at.isoformat() if c.last_sync_at else None,
            "created_at": c.created_at.isoformat(),
        }
        for c in connections
    ]


@router.post("")
async def create_connection(data: ConnectionCreate, db: Session = Depends(get_db)):
    """Add a new accounting system connection."""
    connection = AccountingConnection(
        system_type=data.system_type,
        name=data.name,
    )
    connection.set_config(data.config)
    db.add(connection)
    db.commit()

    return {"id": connection.id, "system_type": connection.system_type}


@router.delete("/{connection_id}")
async def delete_connection(connection_id: int, db: Session = Depends(get_db)):
    """Remove an accounting connection and its cached reference data."""
    connection = db.get(AccountingConnection, connection_id)
    if not connection:
        raise HTTPException(status_code=404, detail="Connection not found")

    db.delete(connection)
    db.commit()

    return {"deleted": True}


@router.get("/{connection_id}/reference-data/{data_type}")
async def get_reference_data(connection_id: int, data_type: str, db: Session = Depends(get_db)):
    """Get cached reference data (vendors, accounts, items, tax_codes) for a connection."""
    if data_type not in ("vendor", "account", "item", "tax_code"):
        raise HTTPException(status_code=400, detail="Invalid data_type")

    items = (
        db.query(ReferenceData)
        .filter(ReferenceData.connection_id == connection_id, ReferenceData.data_type == data_type)
        .order_by(ReferenceData.name)
        .all()
    )

    return [
        {
            "id": item.id,
            "external_id": item.external_id,
            "name": item.name,
            "metadata": item.get_metadata(),
            "synced_at": item.synced_at.isoformat(),
        }
        for item in items
    ]


@router.post("/{connection_id}/sync")
async def sync_reference_data(connection_id: int, db: Session = Depends(get_db)):
    """Trigger a sync of reference data from the accounting system."""
    connection = db.get(AccountingConnection, connection_id)
    if not connection:
        raise HTTPException(status_code=404, detail="Connection not found")

    # TODO: Use the appropriate adapter to fetch and cache reference data
    return {"message": "Sync endpoint — not yet implemented", "connection_id": connection_id}
