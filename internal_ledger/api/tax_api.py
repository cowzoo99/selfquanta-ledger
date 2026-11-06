import frappe

from internal_ledger.accounting.tax_materials import build_tax_material_package as _build
from internal_ledger.accounting.tax_materials import get_tax_material_package as _get


@frappe.whitelist(methods=["POST"])
def build_tax_material_package(company: str, period: str, package_type: str):
    return _build(company, period, package_type)


@frappe.whitelist(methods=["GET", "POST"])
def get_tax_material_package(name: str):
    return _get(name)
