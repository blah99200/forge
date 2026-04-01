"""Accounting system connections and reference data models."""

import json
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class AccountingConnection(Base):
    __tablename__ = "accounting_connections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    system_type: Mapped[str] = mapped_column(String(20), nullable=False)  # "qbo" | "plexxis"
    name: Mapped[str] = mapped_column(String(255), default="")  # User-friendly label
    config: Mapped[str] = mapped_column(Text, default="{}")  # JSON — OAuth tokens, API keys, etc.
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    reference_data: Mapped[list["ReferenceData"]] = relationship(back_populates="connection", cascade="all, delete-orphan")

    def get_config(self) -> dict:
        return json.loads(self.config)

    def set_config(self, config: dict):
        self.config = json.dumps(config)


class ReferenceData(Base):
    __tablename__ = "reference_data"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    connection_id: Mapped[int] = mapped_column(Integer, ForeignKey("accounting_connections.id"), nullable=False)

    data_type: Mapped[str] = mapped_column(String(20), nullable=False)  # "vendor" | "account" | "item" | "tax_code"
    external_id: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")  # Additional attributes
    synced_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    connection: Mapped["AccountingConnection"] = relationship(back_populates="reference_data")

    def get_metadata(self) -> dict:
        return json.loads(self.metadata_json)
