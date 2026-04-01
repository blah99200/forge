"""QuickBooks Online adapter — pushes AP bills and syncs reference data.

Uses the QBO API via python-quickbooks (or direct REST calls).
For the POC, this implements the core adapter interface with QBO-specific logic.
"""

from datetime import datetime, timezone

from app.adapters.base import AccountingAdapter, PushResult


class QBOAdapter(AccountingAdapter):
    """QuickBooks Online adapter."""

    def __init__(self):
        self.client = None
        self.company_id = None

    def connect(self, config: dict) -> bool:
        """Connect to QBO using OAuth2 tokens.

        Config should contain:
            - client_id: QBO app client ID
            - client_secret: QBO app client secret
            - access_token: OAuth2 access token
            - refresh_token: OAuth2 refresh token
            - realm_id: QBO company ID
        """
        try:
            from quickbooks import QuickBooks
            from quickbooks.objects.base import Ref

            self.company_id = config.get("realm_id", "")

            self.client = QuickBooks(
                sandbox=config.get("sandbox", True),
                consumer_key=config.get("client_id", ""),
                consumer_secret=config.get("client_secret", ""),
                access_token=config.get("access_token", ""),
                company_id=self.company_id,
                refresh_token=config.get("refresh_token", ""),
            )
            return True
        except Exception as e:
            raise ConnectionError(f"Failed to connect to QBO: {e}")

    def sync_reference_data(self) -> dict:
        """Fetch vendors, accounts, items, and tax codes from QBO."""
        if not self.client:
            raise RuntimeError("Not connected to QBO")

        from quickbooks.objects.vendor import Vendor
        from quickbooks.objects.account import Account
        from quickbooks.objects.item import Item
        from quickbooks.objects.taxcode import TaxCode

        result = {"vendors": [], "accounts": [], "items": [], "tax_codes": []}

        # Vendors
        vendors = Vendor.all(qb=self.client)
        result["vendors"] = [
            {
                "external_id": str(v.Id),
                "name": v.DisplayName,
                "metadata": {
                    "company_name": v.CompanyName or "",
                    "email": v.PrimaryEmailAddr.Address if v.PrimaryEmailAddr else "",
                },
            }
            for v in vendors
        ]

        # Accounts (expense accounts for AP coding)
        accounts = Account.all(qb=self.client)
        result["accounts"] = [
            {
                "external_id": str(a.Id),
                "name": a.Name,
                "metadata": {
                    "account_type": a.AccountType,
                    "account_sub_type": a.AccountSubType or "",
                },
            }
            for a in accounts
            if a.AccountType in ("Expense", "Cost of Goods Sold", "Other Expense")
        ]

        # Items
        items = Item.all(qb=self.client)
        result["items"] = [
            {
                "external_id": str(i.Id),
                "name": i.Name,
                "metadata": {
                    "type": i.Type,
                    "unit_price": float(i.UnitPrice) if i.UnitPrice else 0,
                },
            }
            for i in items
        ]

        # Tax codes
        tax_codes = TaxCode.all(qb=self.client)
        result["tax_codes"] = [
            {
                "external_id": str(t.Id),
                "name": t.Name,
                "metadata": {},
            }
            for t in tax_codes
        ]

        return result

    def push_bill(self, ap_bill: dict) -> PushResult:
        """Create a Bill in QBO from the standardized AP Bill payload."""
        if not self.client:
            return PushResult(success=False, error="Not connected to QBO")

        try:
            from quickbooks.objects.bill import Bill, BillLine, AccountBasedExpenseLineDetail
            from quickbooks.objects.base import Ref

            bill = Bill()

            # Vendor
            vendor_id = ap_bill.get("vendor", {}).get("external_id", "")
            if vendor_id:
                bill.VendorRef = Ref()
                bill.VendorRef.value = vendor_id

            # Header fields
            bill.DocNumber = ap_bill.get("invoice_number", "")
            bill.TxnDate = ap_bill.get("invoice_date", "")
            bill.DueDate = ap_bill.get("due_date", "")

            # Line items
            bill.Line = []
            for item in ap_bill.get("line_items", []):
                line = BillLine()
                line.Amount = item.get("amount", 0)
                line.Description = item.get("description", "")
                line.DetailType = "AccountBasedExpenseLineDetail"

                detail = AccountBasedExpenseLineDetail()
                account_id = item.get("account_id", "")
                if account_id:
                    detail.AccountRef = Ref()
                    detail.AccountRef.value = account_id

                line.AccountBasedExpenseLineDetail = detail
                bill.Line.append(line)

            # If no line items, create a single line with the total
            if not bill.Line:
                line = BillLine()
                line.Amount = ap_bill.get("total", 0)
                line.DetailType = "AccountBasedExpenseLineDetail"
                detail = AccountBasedExpenseLineDetail()
                line.AccountBasedExpenseLineDetail = detail
                bill.Line.append(line)

            # Save to QBO
            bill.save(qb=self.client)

            return PushResult(
                success=True,
                external_id=str(bill.Id),
                raw_response={"id": bill.Id, "doc_number": bill.DocNumber},
            )

        except Exception as e:
            return PushResult(success=False, error=str(e))

    def get_bill_status(self, external_id: str) -> dict:
        """Check a bill's status in QBO."""
        if not self.client:
            return {"error": "Not connected"}

        try:
            from quickbooks.objects.bill import Bill
            bill = Bill.get(external_id, qb=self.client)
            return {
                "id": bill.Id,
                "doc_number": bill.DocNumber,
                "balance": float(bill.Balance) if bill.Balance else 0,
                "total": float(bill.TotalAmt) if bill.TotalAmt else 0,
            }
        except Exception as e:
            return {"error": str(e)}
