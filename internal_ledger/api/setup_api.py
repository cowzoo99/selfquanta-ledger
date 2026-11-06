import frappe

from internal_ledger.business.setup import apply_business_setup as _apply
from internal_ledger.business.setup import get_setup_status as _status


@frappe.whitelist(methods=["GET", "POST"])
def get_setup_status(company: str) -> dict:
    return _status(company)


@frappe.whitelist(methods=["POST"])
def apply_business_setup(company: str, business_type: str, options=None) -> dict:
    parsed = frappe.parse_json(options) if isinstance(options, str) else options
    return _apply(company, business_type, parsed or {})
