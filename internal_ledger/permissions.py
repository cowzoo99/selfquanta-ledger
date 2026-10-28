import frappe
from frappe.utils import now_datetime


LEDGER_DOCTYPES = ("Import Batch", "Imported Transaction", "Classification Rule", "Voucher Draft",
                  "Posting Link", "Internal Ledger Settings", "Internal Ledger Audit Event",
                  "Internal Ledger Source File")
_AUDIT_INSERT = object()
_SOURCE_INSERT = object()


def require_ledger_admin() -> None:
    if frappe.session.user != "Administrator" and "账套管理员" not in frappe.get_roles():
        frappe.throw("需要账套管理员权限", frappe.PermissionError)


def document_company(doc):
    if doc.doctype == "Imported Transaction" or doc.doctype == "Internal Ledger Source File":
        return frappe.db.get_value("Import Batch", doc.import_batch, "company")
    if doc.doctype == "Posting Link":
        return frappe.db.get_value("Voucher Draft", doc.voucher_draft, "company")
    return doc.get("company")


def has_ledger_permission(doc, ptype, user=None, **kwargs):
    user = user or frappe.session.user
    if user == "Administrator":
        return True
    roles = frappe.get_roles(user)
    admin = "账套管理员" in roles
    if not admin and ("账务查看者" not in roles or ptype not in ("read", "report", "select")):
        return False
    if doc.doctype == "Internal Ledger Source File" and not admin:
        return False
    if doc.doctype == "Internal Ledger Audit Event" and ptype not in ("read", "report", "select"):
        return False
    if doc.doctype == "Internal Ledger Settings":
        return admin or ptype in ("read", "report", "select")
    company = frappe.db.get_single_value("Internal Ledger Settings", "company")
    return bool(company and document_company(doc) == company)


def company_query(user=None, doctype=None):
    if (user or frappe.session.user) == "Administrator":
        return ""
    if doctype in ("Journal Entry", "Account", "GL Entry") and not set(
            frappe.get_roles(user or frappe.session.user)) & {"账套管理员", "账务查看者"}:
        return ""
    company = frappe.db.escape(frappe.db.get_single_value("Internal Ledger Settings", "company") or "")
    table = f"`tab{doctype}`"
    if doctype in ("Imported Transaction", "Internal Ledger Source File"):
        return f"{table}.import_batch IN (SELECT name FROM `tabImport Batch` WHERE company = {company})"
    if doctype == "Posting Link":
        return f"{table}.voucher_draft IN (SELECT name FROM `tabVoucher Draft` WHERE company = {company})"
    return f"{table}.company = {company}"


def has_reporting_permission(doc, ptype, user=None, **kwargs):
    roles = set(frappe.get_roles(user or frappe.session.user))
    if not roles & {"账套管理员", "账务查看者"}:
        return True
    allowed = {"read", "report", "select"}
    if doc.doctype == "Journal Entry" and "账套管理员" in roles:
        allowed |= {"write", "create", "submit"}
        # Frappe's native reverse mapper checks an empty target before mapping company.
        # The actual insert checks this hook again with the populated company.
        if ptype == "create" and doc.is_new() and not doc.company:
            return True
    return ptype in allowed and document_company(doc) == frappe.db.get_single_value(
        "Internal Ledger Settings", "company")


def protect_business_write(doc, method=None):
    require_ledger_admin()


def protect_source_file(doc, method=None):
    old = doc.get_doc_before_save()
    protected = doc.attached_to_doctype == "Internal Ledger Source File" or (
        old and old.attached_to_doctype == "Internal Ledger Source File")
    if protected:
        require_ledger_admin()
        if not doc.is_new() or method == "on_trash" or doc.flags.source_insert is not _SOURCE_INSERT:
            frappe.throw("原始文件只读保存", frappe.PermissionError)
        if not doc.is_private:
            frappe.throw("原始文件必须私有", frappe.PermissionError)


def record_audit(action: str, reference_doctype: str, reference_name: str, result: str) -> None:
    require_ledger_admin()
    reference = frappe.get_doc(reference_doctype, reference_name)
    reference.check_permission("read")
    event = frappe.get_doc({"doctype": "Internal Ledger Audit Event", "action": action,
        "reference_doctype": reference_doctype, "reference_name": reference_name,
        "result": result, "actor": frappe.session.user, "timestamp": now_datetime(),
        "company": document_company(reference)})
    event.flags.audit_insert = _AUDIT_INSERT
    event.insert(ignore_permissions=True)
