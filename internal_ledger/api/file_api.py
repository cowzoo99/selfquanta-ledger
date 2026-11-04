import frappe


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
