import frappe


@frappe.whitelist()
def zip_files(files):
    from frappe.core.api.file import zip_files as native_zip_files

    for name in frappe.parse_json(files):
        if isinstance(name, str):
            file = frappe.get_doc("File", name)
            if not file._ledger_read_allowed():
                frappe.throw("无权读取此文件", frappe.PermissionError)
    return native_zip_files(files)
