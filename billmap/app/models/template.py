"""Vendor template and mapping models."""

import json
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class VendorTemplate(Base):
    __tablename__ = "vendor_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    variant: Mapped[str] = mapped_column(String(50), default="standard")  # "standard", "credit_note", etc.

    # Fingerprinting — stored as JSON strings
    vendor_tokens: Mapped[str] = mapped_column(Text, default="[]")  # ["ACME PTY LTD", "ABN 12345"]
    anchor_positions: Mapped[str] = mapped_column(Text, default="[]")  # [{"label":"Invoice","x":0.45,"y":0.05}]
    layout_grid: Mapped[str] = mapped_column(String(24), default="")  # 24-bit binary string (4x6 grid)
    font_set: Mapped[str] = mapped_column(Text, default="[]")  # ["Helvetica", "Arial"]

    # Auto-push settings
    auto_push_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    confidence_threshold: Mapped[float] = mapped_column(Float, default=0.9)
    min_field_confidence: Mapped[float] = mapped_column(Float, default=0.7)

    # Metadata
    version: Mapped[int] = mapped_column(Integer, default=1)
    sample_pdf_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )

    # Relationships
    field_mappings: Mapped[list["FieldMapping"]] = relationship(back_populates="template", cascade="all, delete-orphan")
    table_config: Mapped["LineItemTableConfig | None"] = relationship(
        back_populates="template", uselist=False, cascade="all, delete-orphan"
    )
    extraction_rules: Mapped[list["ExtractionRule"]] = relationship(
        back_populates="template", cascade="all, delete-orphan"
    )

    def get_vendor_tokens(self) -> list[str]:
        return json.loads(self.vendor_tokens)

    def set_vendor_tokens(self, tokens: list[str]):
        self.vendor_tokens = json.dumps(tokens)

    def get_anchor_positions(self) -> list[dict]:
        return json.loads(self.anchor_positions)

    def set_anchor_positions(self, positions: list[dict]):
        self.anchor_positions = json.dumps(positions)

    def get_font_set(self) -> list[str]:
        return json.loads(self.font_set)

    def set_font_set(self, fonts: list[str]):
        self.font_set = json.dumps(fonts)


class FieldMapping(Base):
    __tablename__ = "field_mappings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    template_id: Mapped[int] = mapped_column(Integer, ForeignKey("vendor_templates.id"), nullable=False)

    field_name: Mapped[str] = mapped_column(String(100), nullable=False)  # "vendor_name", "invoice_number", etc.
    field_type: Mapped[str] = mapped_column(String(20), default="header")  # "header" | "line_item_column"

    # Extraction region — normalized coordinates (0-1 range relative to page size)
    region: Mapped[str] = mapped_column(Text, nullable=False)  # JSON: {"page": 0, "x": 0.1, "y": 0.2, "w": 0.3, "h": 0.05}

    extraction_method: Mapped[str] = mapped_column(String(20), default="text_region")  # "text_region" | "regex" | "ocr_region"
    post_processing: Mapped[str] = mapped_column(Text, default="{}")  # JSON: {"trim": true, "date_format": "MM/DD/YYYY", ...}

    template: Mapped["VendorTemplate"] = relationship(back_populates="field_mappings")

    def get_region(self) -> dict:
        return json.loads(self.region)

    def set_region(self, region: dict):
        self.region = json.dumps(region)

    def get_post_processing(self) -> dict:
        return json.loads(self.post_processing)


class LineItemTableConfig(Base):
    __tablename__ = "line_item_table_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    template_id: Mapped[int] = mapped_column(Integer, ForeignKey("vendor_templates.id"), unique=True, nullable=False)

    # Bounding box for the table area
    table_region: Mapped[str] = mapped_column(Text, nullable=False)  # JSON: {"page": 0, "x": 0.05, "y": 0.4, "w": 0.9, "h": 0.4}

    # Column definitions
    column_definitions: Mapped[str] = mapped_column(Text, nullable=False)  # JSON: [{"name": "description", "x": 0.05, "w": 0.3}, ...]

    row_detection: Mapped[str] = mapped_column(String(30), default="line_separated")  # "fixed_height" | "line_separated"

    template: Mapped["VendorTemplate"] = relationship(back_populates="table_config")

    def get_table_region(self) -> dict:
        return json.loads(self.table_region)

    def get_column_definitions(self) -> list[dict]:
        return json.loads(self.column_definitions)


class ExtractionRule(Base):
    __tablename__ = "extraction_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    template_id: Mapped[int] = mapped_column(Integer, ForeignKey("vendor_templates.id"), nullable=False)

    rule_type: Mapped[str] = mapped_column(String(20), nullable=False)  # "lookup" | "conditional" | "transform"
    source_field: Mapped[str] = mapped_column(String(100), nullable=False)
    target_field: Mapped[str] = mapped_column(String(100), nullable=False)
    rule_config: Mapped[str] = mapped_column(Text, nullable=False)  # JSON — varies by rule_type

    template: Mapped["VendorTemplate"] = relationship(back_populates="extraction_rules")

    def get_rule_config(self) -> dict:
        return json.loads(self.rule_config)
