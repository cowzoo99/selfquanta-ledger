import frappe

from internal_ledger.accounting.tax_materials import build_tax_material_package as _build
from internal_ledger.accounting.tax_materials import get_tax_material_package as _get


@frappe.whitelist(methods=["POST"])
def build_tax_material_package(company: str, period: str, package_type: str):
    return _build(company, period, package_type)


@frappe.whitelist(methods=["GET", "POST"])
def get_tax_material_package(name: str):
    return _get(name)


@frappe.whitelist(methods=["GET", "POST"])
def get_latest_tax_material_package(period: str, package_type: str):
    company = frappe.db.get_single_value("Internal Ledger Settings", "company")
    rows = frappe.get_list("Tax Material Package", filters={"company": company, "period": period,
        "package_type": package_type, "status": "Ready"}, fields=["name"], order_by="version desc", limit=1)
    return _get(rows[0].name) if rows else None
