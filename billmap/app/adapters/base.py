"""Abstract base for accounting system adapters."""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class PushResult:
    """Result of pushing a bill to an accounting system."""
    success: bool
    external_id: str = ""
    error: str = ""
    raw_response: dict | None = None


class AccountingAdapter(ABC):
    """Interface for accounting system adapters (QBO, Plexxis, etc.)."""

    @abstractmethod
    def connect(self, config: dict) -> bool:
        """Initialize connection using config (OAuth tokens, API keys, etc.)."""
        ...

    @abstractmethod
    def sync_reference_data(self) -> dict:
        """Fetch reference data from the accounting system.

        Returns dict with keys: vendors, accounts, items, tax_codes.
        Each is a list of {"external_id": ..., "name": ..., "metadata": {...}}.
        """
        ...

    @abstractmethod
    def push_bill(self, ap_bill: dict) -> PushResult:
        """Push an AP bill payload to the accounting system."""
        ...

    @abstractmethod
    def get_bill_status(self, external_id: str) -> dict:
        """Check the status of a previously pushed bill."""
        ...
