from pathlib import Path
from posixpath import split
from uuid import uuid4

import frappe
from frappe.utils import now_datetime


LEDGER_DOCTYPES = ("Import Batch", "Imported Transaction", "Classification Rule", "Voucher Draft",
                  "Posting Link", "Internal Ledger Settings", "Internal Ledger Audit Event",
                  "Internal Ledger Source File", "Ledger Evidence")
_AUDIT_INSERT = object()
_SOURCE_INSERT = object()
_IMPORT_WRITE = object()
_POSTED_ENTRY_REBUILD = object()
_JOURNAL_WRITE = object()
_JOURNAL_DELETE = object()
_EVIDENCE_WRITE = object()
_EVIDENCE_FILE_WRITE = object()
_NATIVE_FILE_DEDUP = object()
_ZIP_READ_BYTES = object()
_PROTECTED_FILES = ("Ledger Evidence", "Internal Ledger Source File")


def _physical_file_path(url, is_private=1, deletion=False):
    if not url:
        return None
    from frappe.utils import get_url
    site_url = get_url()
    if url.startswith(site_url) and "/files/" in url:
        url = url[len(site_url):]
    elif not deletion and url.startswith(("http://", "https://")):
        return None
    if ".." in url.split("/"):
        frappe.throw("文件路径无效", frappe.PermissionError)
    private = Path(frappe.get_site_path("private", "files")).resolve()
    public = Path(frappe.get_site_path("public", "files")).resolve()
    if deletion:
        directory, name = split(url.strip("/"))
        root = public if directory == "files" else private
        path = root / name
    elif url.startswith("/private/files/"):
        root, path = private, private / url[len("/private/files/"):].lstrip("/")
    elif url.startswith("/files/"):
        root, path = public, public / url[len("/files/"):].lstrip("/")
    elif "/" not in url:
        root = private if is_private else public
        path = root / url
    else:
        return None
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        frappe.throw("文件路径无效", frappe.PermissionError)
    return resolved


def _protected_file_rows(current=False):
    return frappe.db.get_values("File", {"attached_to_doctype": ["in", list(_PROTECTED_FILES)]},
        ["name", "file_url", "thumbnail_url", "content_hash", "is_private", "attached_to_doctype", "attached_to_name"],
        as_dict=True, for_update=current)


def _matching_protected_files(url, is_private=1, current=False):
    path = _physical_file_path(url, is_private)
    if path is None:
        return []
    return [row for row in _protected_file_rows(current)
            if _physical_file_path(row.file_url, row.is_private) == path]


def _can_read_protected(row, user, roles, company):
    if not company or (user != "Administrator" and not roles & {"账套管理员", "账务查看者"}):
        return False
    if row.attached_to_doctype == "Internal Ledger Source File" and user != "Administrator" and "账套管理员" not in roles:
        return False
    try:
        return document_company(frappe.get_doc(row.attached_to_doctype, row.attached_to_name)) == company
    except frappe.DoesNotExistError:
        return False


def require_ledger_admin() -> None:
    if frappe.session.user != "Administrator" and "账套管理员" not in frappe.get_roles():
        frappe.throw("需要账套管理员权限", frappe.PermissionError)


def document_company(doc):
    if doc.doctype == "Company":
        return doc.name
    if doc.doctype == "Imported Transaction" or doc.doctype == "Internal Ledger Source File":
        return frappe.db.get_value("Import Batch", doc.import_batch, "company")
    if doc.doctype == "Ledger Evidence":
        if not doc.imported_transaction:
            return None
        batch = frappe.db.get_value("Imported Transaction", doc.imported_transaction, "import_batch")
        return frappe.db.get_value("Import Batch", batch, "company") if batch else None
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
    if doctype == "Ledger Evidence":
        return (f"{table}.imported_transaction IN (SELECT name FROM `tabImported Transaction` "
                f"WHERE import_batch IN (SELECT name FROM `tabImport Batch` WHERE company = {company}))")
    if doctype in ("Imported Transaction", "Internal Ledger Source File"):
        return f"{table}.import_batch IN (SELECT name FROM `tabImport Batch` WHERE company = {company})"
    if doctype == "Posting Link":
        return f"{table}.voucher_draft IN (SELECT name FROM `tabVoucher Draft` WHERE company = {company})"
    return f"{table}.company = {company}"


def file_query(user=None, doctype=None):
    user = user or frappe.session.user
    protected = _protected_file_rows()
    roles = set(frappe.get_roles(user))
    company = frappe.db.get_single_value("Internal Ledger Settings", "company")
    denied = [row for row in protected if not _can_read_protected(row, user, roles, company)]
    denied_names = {row.name for row in denied}
    denied_paths = {_physical_file_path(row.file_url, row.is_private) for row in denied if row.file_url} - {None}
    files = frappe.db.get_values("File", {}, fieldname=["name", "file_url", "is_private"], as_dict=True)
    denied_names.update(row.name for row in files if row.file_url and
                        _physical_file_path(row.file_url, row.is_private) in denied_paths)
    return ("`tabFile`.name NOT IN (" + ", ".join(frappe.db.escape(name) for name in denied_names) + ")") if denied_names else ""


def cleanup_ledger_file_shares():
    frappe.db.sql("SELECT value FROM `tabSingles` WHERE doctype = %s AND field = %s FOR UPDATE",
                  ("Internal Ledger Settings", "company"))
    protected = _protected_file_rows(current=True)
    paths = {_physical_file_path(row.file_url, row.is_private) for row in protected if row.file_url} - {None}
    files = frappe.db.get_values("File", {}, fieldname=["name", "file_url", "is_private"], as_dict=True, for_update=True)
    names = [row.name for row in files if row.file_url and _physical_file_path(row.file_url, row.is_private) in paths]
    names.extend(row.name for row in protected)
    if names:
        frappe.db.delete("DocShare", {"share_doctype": "File", "share_name": ["in", names]})


def file_permission(doc, ptype, user=None, **kwargs):
    user = user or frappe.session.user
    if ptype not in ("read", "select") or doc._ledger_read_allowed(user):
        return True
    # A false controller result is followed by Frappe's share fallback.
    if frappe.share.get_shared("File", user=user, rights=["read"], filters=[["share_name", "=", doc.name]]):
        frappe.throw("文件分享权限已失效，请联系管理员", frappe.PermissionError)
    return False


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


def protect_evidence_write(doc, method=None):
    require_ledger_admin()
    if doc.flags.ledger_evidence_write is not _EVIDENCE_WRITE:
        frappe.throw("流水备注与附件只能由佐证服务写入", frappe.PermissionError)


def protect_journal_write(doc, method=None):
    company = frappe.db.get_single_value("Internal Ledger Settings", "company")
    old = doc.get_doc_before_save()
    if company and (doc.company == company or (old and old.company == company)):
        require_ledger_admin()
        service_delete = method == "on_trash" and frappe.flags.get("ledger_journal_delete") is _JOURNAL_DELETE
        if doc.flags.ledger_journal_write is not _JOURNAL_WRITE and not service_delete:
            frappe.throw("当前账套凭证只能由过账或冲销服务写入", frappe.PermissionError)


def protect_source_file(doc, method=None):
    # Serialize file-domain checks with creation and privacy changes on this single-site ledger.
    frappe.db.sql("SELECT value FROM `tabSingles` WHERE doctype = %s AND field = %s FOR UPDATE",
                  ("Internal Ledger Settings", "company"))
    old = doc.get_doc_before_save()
    protected = doc.attached_to_doctype == "Internal Ledger Source File" or (
        old and old.attached_to_doctype == "Internal Ledger Source File")
    if protected:
        require_ledger_admin()
        if not doc.is_new() or method == "on_trash" or doc.flags.source_insert is not _SOURCE_INSERT:
            frappe.throw("原始文件只读保存", frappe.PermissionError)
        if not doc.is_private:
            frappe.throw("原始文件必须私有", frappe.PermissionError)
    evidence = doc.attached_to_doctype == "Ledger Evidence" or (
        old and old.attached_to_doctype == "Ledger Evidence")
    if evidence:
        require_ledger_admin()
        if doc.flags.evidence_file_write is not _EVIDENCE_FILE_WRITE:
            frappe.throw("流水备注与附件只能由佐证服务写入", frappe.PermissionError)
        if doc.is_private != 1:
            frappe.throw("流水附件必须私有", frappe.PermissionError)
    if not protected and not evidence:
        fields = ("file_url", "thumbnail_url")
        paths = [doc.get(field) for field in fields
                 if (method == "on_trash" or not old or doc.get(field) != old.get(field))
                 and not (field == "file_url" and method != "on_trash" and doc.is_new()
                          and doc.flags.ledger_native_dedup is _NATIVE_FILE_DEDUP)]
        if paths:
            protected_files = frappe.db.get_values("File", filters={"name": ["!=", doc.name],
                "attached_to_doctype": ["in", ["Ledger Evidence", "Internal Ledger Source File"]]},
                fieldname=["file_url", "thumbnail_url", "content_hash", "is_private"], as_dict=True, for_update=True)
            if (method == "on_trash" and doc.file_url and doc.content_hash and doc._ledger_read_allowed(current=True)
                    and any(_physical_file_path(row.file_url, row.is_private) == _physical_file_path(doc.file_url, doc.is_private)
                            and row.content_hash == doc.content_hash
                            for row in protected_files)):
                paths = [path for path in paths if path != doc.file_url or path == doc.thumbnail_url]
            protected_paths = {_physical_file_path(row.get(field), row.is_private)
                               for row in protected_files for field in fields if row.get(field)} - {None}
            if any(_physical_file_path(path, doc.is_private, deletion=deletion) in protected_paths
                   for path in paths if path for deletion in (False, True)):
                frappe.throw("不能引用或删除受保护的流水文件", frappe.PermissionError)


class ProtectedLedgerFile:
    def validate_share(self, share):
        frappe.db.sql("SELECT value FROM `tabSingles` WHERE doctype = %s AND field = %s FOR UPDATE",
                      ("Internal Ledger Settings", "company"))
        protected = (self.attached_to_doctype in _PROTECTED_FILES or
                     _matching_protected_files(self.file_url, self.is_private, current=True))
        if protected:
            frappe.throw("流水原始文件与佐证不能单独分享", frappe.PermissionError)

    def _ledger_read_allowed(self, user=None, current=False):
        user = user or frappe.session.user
        references = _matching_protected_files(self.file_url, self.is_private, current=current)
        if self.attached_to_doctype in ("Ledger Evidence", "Internal Ledger Source File"):
            references.append(self)
        roles = set(frappe.get_roles(user))
        company = frappe.db.get_single_value("Internal Ledger Settings", "company")
        return all(_can_read_protected(row, user, roles, company) for row in references)

    def has_permission(self, permtype="read", *, debug=False, user=None):
        if permtype in ("read", "select") and not self._ledger_read_allowed(user):
            return False
        return super().has_permission(permtype, debug=debug, user=user)

    def is_downloadable(self):
        return self._ledger_read_allowed() and super().is_downloadable()

    def get_content(self, encodings=None):
        if self.file_url and not self._ledger_read_allowed():
            frappe.throw("无权读取此文件", frappe.PermissionError)
        if self.flags.ledger_zip_read is _ZIP_READ_BYTES:
            encodings = []
        return super().get_content(encodings=encodings)

    def _is_controlled_attachment(self):
        return ((self.attached_to_doctype == "Ledger Evidence"
                 and self.flags.evidence_file_write is _EVIDENCE_FILE_WRITE)
                or (self.attached_to_doctype == "Internal Ledger Source File"
                    and self.flags.source_insert is _SOURCE_INSERT))

    def save_file(self, *args, **kwargs):
        protect_source_file(self, "save_file")
        if self.is_new() and self._is_controlled_attachment():
            # Native File can reuse a URL both during storage and after storage on a duplicate assignment.
            kwargs["ignore_existing_file_check"] = True
            self.flags.ignore_duplicate_entry_error = True
            # URL authorization uses a case-insensitive collation. Reserve distinct storage names.
            domain = "evidence" if self.attached_to_doctype == "Ledger Evidence" else "source"
            prefix = f"{domain}-{uuid4().hex}-"
            display_name = self.file_name
            suffix_bytes = 255 - len(prefix)
            self.file_name = prefix + display_name.encode("utf-8")[-suffix_bytes:].decode("utf-8", errors="ignore")
            try:
                return super().save_file(*args, **kwargs)
            finally:
                self.file_name = display_name
        if not self.is_new() and not self.is_remote_file:
            self._protect_shared_blob()
        generated = self.is_new() and not self.file_url
        result = super().save_file(*args, **kwargs)
        if generated:
            self.flags.ledger_native_dedup = _NATIVE_FILE_DEDUP
        return result

    def write_file(self):
        if not self.is_new() or not self._is_controlled_attachment():
            return super().write_file()
        # Reserve only the final, disambiguated path before native validation/write/fsync.
        path = Path(self.get_full_path())
        path.touch(exist_ok=False)
        try:
            return super().write_file()
        except Exception:
            path.unlink(missing_ok=True)
            raise

    def _delete_file_on_disk(self):
        controlled_hash = self.content_hash and frappe.db.get_values("File", filters={
            "name": ["!=", self.name], "content_hash": self.content_hash,
            "attached_to_doctype": ["in", ["Ledger Evidence", "Internal Ledger Source File"]]},
            fieldname="name", for_update=True)
        if not self.file_url or not (self._is_controlled_attachment() or controlled_hash):
            return super()._delete_file_on_disk()
        references = frappe.db.get_values("File", {"name": ["!=", self.name]},
            ["file_url", "is_private"], as_dict=True, for_update=True)
        # Distinct paths with identical bytes must be unlinked independently.
        target = _physical_file_path(self.file_url, self.is_private, deletion=True)
        return self.delete_file_data_content(only_thumbnail=any(
            row.file_url and _physical_file_path(row.file_url, row.is_private) == target for row in references))

    def handle_is_private_changed(self):
        protect_source_file(self, "handle_is_private_changed")
        if not self.is_remote_file:
            self._protect_shared_blob()
        return super().handle_is_private_changed()

    def _protect_shared_blob(self):
        path = _physical_file_path(self.file_url, self.is_private)
        if any(row.name != self.name and ((path is not None and
                _physical_file_path(row.file_url, row.is_private) == path) or
                (self.content_hash and row.content_hash == self.content_hash))
               for row in _protected_file_rows(current=True)):
            frappe.throw("共享流水佐证或原始文件的附件不能改写或公开", frappe.PermissionError)

    def on_trash(self):
        # File's native on_trash removes the blob before doc_events run.
        protect_source_file(self, "on_trash")
        return super().on_trash()


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
