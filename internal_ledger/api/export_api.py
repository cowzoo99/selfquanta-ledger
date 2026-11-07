import frappe

from internal_ledger.customer_exit import service


@frappe.whitelist(methods=["POST"])
def request_customer_exit_export():
    return service.request_customer_exit_export()


@frappe.whitelist(methods=["GET"])
def list_customer_exit_exports():
    return service.list_customer_exit_exports()


@frappe.whitelist(methods=["GET"])
def download_customer_exit_export(name: str):
    return service.download_customer_exit_export(name)


@frappe.whitelist(methods=["POST"])
def delete_customer_exit_export(name: str):
    return service.delete_customer_exit_export(name)
