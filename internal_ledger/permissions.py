import frappe
from frappe.utils import now_datetime


LEDGER_DOCTYPES = ("Import Batch", "Imported Transaction", "Classification Rule", "Voucher Draft",
                  "Posting Link", "Internal Ledger Settings", "Internal Ledger Audit Event",
                  "Internal Ledger Source File")
_AUDIT_INSERT = object()
_SOURCE_INSERT = object()
_IMPORT_WRITE = object()
_JOURNAL_WRITE = object()


def require_ledger_admin() -> None:
    if frappe.session.user != "Administrator" and "账套管理员" not in frappe.get_roles():
        frappe.throw("需要账套管理员权限", frappe.PermissionError)


def document_company(doc):
    if doc.doctype == "Company":
        return doc.name
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


def protect_import_write(doc, method=None):
    require_ledger_admin()
    if doc.flags.ledger_import_write is not _IMPORT_WRITE:
        frappe.throw("导入批次与流水只能由导入服务写入", frappe.PermissionError)


def protect_journal_write(doc, method=None):
    company = frappe.db.get_single_value("Internal Ledger Settings", "company")
    old = doc.get_doc_before_save()
    if company and (doc.company == company or (old and old.company == company)):
        require_ledger_admin()
        if doc.flags.ledger_journal_write is not _JOURNAL_WRITE:
            frappe.throw("当前账套凭证只能由过账或冲销服务写入", frappe.PermissionError)


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
    _insert_audit(action, reference, result)


def record_member_audit(action: str, user: str, company: str) -> None:
    require_ledger_admin()
    if company != frappe.db.get_single_value("Internal Ledger Settings", "company"):
        frappe.throw("不能记录其他企业的成员操作", frappe.PermissionError)
    event = frappe.get_doc({"doctype": "Internal Ledger Audit Event", "action": action,
        "reference_doctype": "User", "reference_name": user, "result": "success",
        "actor": frappe.session.user, "timestamp": now_datetime(), "company": company})
    event.flags.audit_insert = _AUDIT_INSERT
    event.insert(ignore_permissions=True)


def _insert_audit(action, reference, result, deleted=False):
    require_ledger_admin()
    event = frappe.get_doc({"doctype": "Internal Ledger Audit Event", "action": action,
        "reference_doctype": reference.doctype, "reference_name": reference.name,
        "result": result, "actor": frappe.session.user, "timestamp": now_datetime(),
        "company": document_company(reference)})
    event.flags.audit_insert = _AUDIT_INSERT
    event.insert(ignore_permissions=True, ignore_links=deleted)


def audit_configuration_write(doc, method=None):
    if method == "after_delete":
        _insert_audit("delete_classification_rule", doc, "success", deleted=True)
        return
    old = doc.get_doc_before_save()
    if old and not any(doc.get(field.fieldname) != old.get(field.fieldname) for field in doc.meta.fields):
        return
    if doc.doctype == "Internal Ledger Settings":
        action = "update_settings"
    else:
        action = "update_classification_rule" if old else "create_classification_rule"
    record_audit(action, doc.doctype, doc.name, "success")


def audit_install_change(action, reference_doctype, reference_name):
    if not frappe.db.table_exists("Internal Ledger Audit Event"):
        if frappe.flags.in_install or frappe.flags.in_migrate:
            return
        frappe.throw("审计表未就绪，不能保存安装变更")
    record_audit(action, reference_doctype, reference_name, "success")
