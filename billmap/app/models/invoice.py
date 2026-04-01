"""Invoice and batch processing models."""

import json
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Batch(Base):
    __tablename__ = "batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # "pending", "processing", "completed", "failed"
    invoice_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    invoices: Mapped[list["Invoice"]] = relationship(back_populates="batch")


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    # Template association (null if unknown vendor)
    template_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("vendor_templates.id"), nullable=True)
    batch_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("batches.id"), nullable=True)

    # Processing status
    # pending → classified → extracted → reviewed → approved → pushed | failed
    status: Mapped[str] = mapped_column(String(20), default="pending")

    # Extraction results
    raw_extraction: Mapped[str] = mapped_column(Text, default="{}")  # JSON — raw extracted data
    final_payload: Mapped[str] = mapped_column(Text, default="{}")  # JSON — the ap_bill payload

    # Confidence scores
    confidence_overall: Mapped[float | None] = mapped_column(Float, nullable=True)
    field_confidences: Mapped[str] = mapped_column(Text, default="{}")  # JSON: {"vendor_name": 0.95, "total": 0.88, ...}

    # Accounting target
    accounting_system: Mapped[str | None] = mapped_column(String(20), nullable=True)  # "qbo" | "plexxis"
    external_id: Mapped[str | None] = mapped_column(String(100), nullable=True)  # ID in accounting system after push

    # Metadata
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )

    # Relationships
    template: Mapped["VendorTemplate | None"] = relationship()
    batch: Mapped["Batch | None"] = relationship(back_populates="invoices")

    def get_raw_extraction(self) -> dict:
        return json.loads(self.raw_extraction)

    def set_raw_extraction(self, data: dict):
        self.raw_extraction = json.dumps(data)

    def get_final_payload(self) -> dict:
        return json.loads(self.final_payload)

    def set_final_payload(self, payload: dict):
        self.final_payload = json.dumps(payload)

    def get_field_confidences(self) -> dict:
        return json.loads(self.field_confidences)

    def set_field_confidences(self, confidences: dict):
        self.field_confidences = json.dumps(confidences)


# Import for relationship resolution
from app.models.template import VendorTemplate  # noqa: E402, F401
