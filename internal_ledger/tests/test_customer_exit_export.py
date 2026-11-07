import unittest
from pathlib import Path

import frappe

from internal_ledger.tests.test_permissions import user
from internal_ledger.tests.test_review_api import set_up, tear_down


def owner():
    return user("企业负责人")


def clean_up():
    frappe.set_user("Administrator")
    if frappe.db.table_exists("Customer Exit Export"):
        frappe.db.delete("Customer Exit Export")
    tear_down()


def test_only_explicit_enterprise_owner_passes_export_gate():
    from internal_ledger.permissions import require_enterprise_owner

    owner_user = owner()
    nonowners = (user("账套管理员"), user("账务查看者"), "Administrator", "Guest")
    frappe.set_user(owner_user)
    assert require_enterprise_owner() == frappe.session.user
    for actor in nonowners:
        frappe.set_user(actor)
        with unittest.TestCase().assertRaises(frappe.PermissionError):
            require_enterprise_owner()


def test_customer_exit_request_is_owner_read_only_and_service_written():
    from internal_ledger.customer_exit.service import _insert_request

    frappe.set_user(owner())
    request = _insert_request(frappe.session.user)
    assert request.status == "Generating"
    assert request.requested_by == frappe.session.user
    assert frappe.has_permission(request.doctype, "read", request)
    with unittest.TestCase().assertRaises(frappe.PermissionError):
        request.status = "Ready"
        request.save()


def test_archive_contains_business_rows_attachment_audit_and_valid_hashes():
    import hashlib
    import io
    import json
    from zipfile import ZipFile

    from internal_ledger.customer_exit.archive import build_customer_exit_archive
    from internal_ledger.customer_exit.service import _insert_request

    marker = "EXIT-OWN-FIXTURE"
    customer = frappe.get_doc({
        "doctype": "Customer",
        "customer_name": marker,
        "customer_type": "Company",
    }).insert(ignore_permissions=True)
    attached = frappe.get_doc({
        "doctype": "File",
        "file_name": "退出凭据.txt",
        "content": marker.encode("utf-8"),
        "is_private": 1,
        "attached_to_doctype": "Customer",
        "attached_to_name": customer.name,
    }).insert(ignore_permissions=True)
    frappe.set_user(owner())
    request = _insert_request(frappe.session.user)
    target = Path(frappe.get_site_path("private", f"{request.name}.zip"))
    try:
        manifest = build_customer_exit_archive(request.name, target)
        content = target.read_bytes()
        assert len(hashlib.sha256(content).hexdigest()) == 64
        with ZipFile(io.BytesIO(content)) as archive:
            names = archive.namelist()
            assert names[:2] == ["manifest.json", "README.txt"]
            assert "master-data/customer.csv" in names
            assert "audit/internal-ledger-audit-event.csv" in names
            assert "attachments/index.csv" in names
            assert marker in archive.read("master-data/customer.csv").decode("utf-8-sig")
            index = archive.read("attachments/index.csv").decode("utf-8-sig")
            assert attached.name in index and attached.file_name in index
            file_name = next(name for name in names if name.startswith("attachments/files/"))
            assert archive.read(file_name) == marker.encode("utf-8")
            stored = json.loads(archive.read("manifest.json"))
            assert stored["schema"] == "selfledger.customer-exit.v1"
            assert stored["row_count"] == manifest["row_count"]
            assert all(len(item["sha256"]) == 64 for item in stored["members"])
    finally:
        target.unlink(missing_ok=True)


def test_archive_excludes_authentication_and_server_material():
    from internal_ledger.customer_exit.catalog import safe_fields

    fields = set(safe_fields(frappe.get_meta("User")))
    assert "password" not in fields
    assert "api_key" not in fields
    assert "api_secret" not in fields
    assert "reset_password_key" not in fields
    assert "name" in fields


def test_attachment_name_cannot_escape_archive():
    from internal_ledger.customer_exit.archive import attachment_archive_path

    path = attachment_archive_path("FILE-0001", "../../server.key")
    assert path.startswith("attachments/files/")
    assert ".." not in path
    assert "server.key" not in path


def test_owner_request_completes_private_package_and_records_audit():
    from internal_ledger.api.export_api import list_customer_exit_exports, request_customer_exit_export

    frappe.set_user(owner())
    result = request_customer_exit_export()
    assert result["status"] == "Ready"
    assert result["requested_by"] == frappe.session.user
    assert result["file"].startswith("/private/files/")
    assert len(result["package_sha256"]) == 64
    assert list_customer_exit_exports()[0]["name"] == result["name"]
    actions = frappe.get_all("Internal Ledger Audit Event", filters={
        "reference_doctype": "Customer Exit Export", "reference_name": result["name"],
    }, pluck="action")
    assert "request_customer_exit_export" in actions
    assert "complete_customer_exit_export" in actions


def test_nonowners_cannot_request_list_download_or_delete():
    from internal_ledger.api import export_api

    actors = (user("账套管理员"), user("账务查看者"), "Administrator", "Guest")
    frappe.set_user(owner())
    result = export_api.request_customer_exit_export()
    for actor in actors:
        frappe.set_user(actor)
        for operation in (
            export_api.request_customer_exit_export,
            export_api.list_customer_exit_exports,
            lambda: export_api.download_customer_exit_export(result["name"]),
            lambda: export_api.delete_customer_exit_export(result["name"]),
        ):
            with unittest.TestCase().assertRaises(frappe.PermissionError):
                operation()


def test_missing_attachment_fails_without_downloadable_partial_package():
    from internal_ledger.api.export_api import request_customer_exit_export

    customer = frappe.get_doc({
        "doctype": "Customer",
        "customer_name": "EXIT-MISSING-FILE",
        "customer_type": "Company",
    }).insert(ignore_permissions=True)
    missing = frappe.get_doc({
        "doctype": "File",
        "file_name": "missing.txt",
        "content": b"removed after insert",
        "is_private": 1,
        "attached_to_doctype": "Customer",
        "attached_to_name": customer.name,
    }).insert(ignore_permissions=True)
    Path(missing.get_full_path()).unlink()
    frappe.set_user(owner())
    result = request_customer_exit_export()
    assert result["status"] == "Failed"
    assert result["failure_code"] == "ATTACHMENT_READ_FAILED"
    assert not result["file"]
    assert not frappe.get_all("File", filters={
        "attached_to_doctype": "Customer Exit Export", "attached_to_name": result["name"],
    })


def test_owner_delete_removes_package_and_is_idempotent():
    from internal_ledger.api.export_api import delete_customer_exit_export, request_customer_exit_export

    frappe.set_user(owner())
    result = request_customer_exit_export()
    package = frappe.db.get_value("File", {
        "attached_to_doctype": "Customer Exit Export", "attached_to_name": result["name"],
    }, "name")
    deleted = delete_customer_exit_export(result["name"])
    assert deleted["status"] == "Deleted" and not deleted["file"]
    assert not frappe.db.exists("File", package)
    assert delete_customer_exit_export(result["name"]) == deleted


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(
        unittest.FunctionTestCase(test, setUp=set_up, tearDown=clean_up)
        for name, test in sorted(globals().items())
        if name.startswith("test_")
    )
