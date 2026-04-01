"""Plexxis Dataverse adapter — stub for AP bill integration.

This adapter will be implemented once the Plexxis Dataverse API docs are available.
For now, it follows the same AccountingAdapter interface as QBO.
"""

from app.adapters.base import AccountingAdapter, PushResult


class PlexxisAdapter(AccountingAdapter):
    """Plexxis Dataverse adapter (stub)."""

    def __init__(self):
        self.base_url = ""
        self.api_key = ""

    def connect(self, config: dict) -> bool:
        """Connect to Plexxis Dataverse API.

        Config should contain:
            - base_url: Plexxis Dataverse API base URL
            - api_key: API authentication key
        """
        self.base_url = config.get("base_url", "")
        self.api_key = config.get("api_key", "")

        if not self.base_url or not self.api_key:
            raise ConnectionError("Plexxis adapter requires base_url and api_key")

        # TODO: Verify connection with a test API call
        return True

    def sync_reference_data(self) -> dict:
        """Fetch reference data from Plexxis Dataverse.

        TODO: Implement once API endpoints are known.
        Expected endpoints for:
        - Vendors / Subcontractors
        - GL Accounts / Cost Codes
        - Items / Materials
        - Tax Codes
        """
        raise NotImplementedError("Plexxis reference data sync not yet implemented — awaiting API docs")

    def push_bill(self, ap_bill: dict) -> PushResult:
        """Push an AP bill to Plexxis Dataverse.

        TODO: Implement once the AP invoice/bill creation endpoint is known.
        """
        raise NotImplementedError("Plexxis bill push not yet implemented — awaiting API docs")

    def get_bill_status(self, external_id: str) -> dict:
        """Check bill status in Plexxis.

        TODO: Implement once API docs are available.
        """
        raise NotImplementedError("Plexxis bill status not yet implemented — awaiting API docs")
