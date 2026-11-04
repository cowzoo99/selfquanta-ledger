import frappe
from pathlib import Path


@frappe.whitelist()
def zip_files(files):
    from frappe.core.doctype.file.file import File
    from internal_ledger.permissions import _ZIP_READ_BYTES

    selected = []
    for name in frappe.parse_json(files):
        if isinstance(name, str):
            file = frappe.get_doc("File", name)
            if not file._ledger_read_allowed():
                frappe.throw("无权读取此文件", frappe.PermissionError)
            file.flags.ledger_zip_read = _ZIP_READ_BYTES
            selected.append(file)
        else:
            selected.append(name)
    frappe.response["filename"] = "files.zip"
    frappe.response["filecontent"] = File.zip_files(selected)
    frappe.response["type"] = "download"


@frappe.whitelist(methods=["POST"])
def convert_mt940_to_csv(data_import, mt940_file_path):
    from erpnext.accounts.doctype.bank_statement_import.bank_statement_import import convert_mt940_to_csv as native_convert
    from frappe.utils.file_manager import get_file_path
    from internal_ledger.permissions import _physical_file_path, _protected_file_rows

    frappe.get_doc("Bank Statement Import", data_import).check_permission("write")
    path = get_file_path(mt940_file_path)
    if not path:
        frappe.throw("文件路径无效", frappe.PermissionError)
    path = Path(path).resolve()
    for row in _protected_file_rows():
        if _physical_file_path(row.file_url, row.is_private) == path:
            if not frappe.get_doc("File", row.name)._ledger_read_allowed():
                frappe.throw("无权读取此文件", frappe.PermissionError)
    return native_convert(data_import, mt940_file_path)
