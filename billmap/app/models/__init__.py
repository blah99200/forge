"""Import all models so Base.metadata sees them."""

from app.models.template import VendorTemplate, FieldMapping, LineItemTableConfig, ExtractionRule  # noqa: F401
from app.models.invoice import Invoice, Batch  # noqa: F401
from app.models.connection import AccountingConnection, ReferenceData  # noqa: F401
