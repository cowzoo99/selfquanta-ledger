import unittest
from pathlib import Path
from uuid import uuid4

import frappe

from internal_ledger.tests.test_classifier import set_up, transaction
from internal_ledger.tests.test_permissions import user


def test_direct_evidence_insert_is_blocked():
    name = transaction()
    for actor in (user("账套管理员"), "Administrator"):
        frappe.set_user(actor)
        doc = frappe.get_doc({"doctype": "Ledger Evidence", "imported_transaction": name,
                              "note_html": "核对说明"})
        with unittest.TestCase().assertRaises(frappe.PermissionError):
            doc.insert()


def test_viewer_reads_evidence_metadata_but_cannot_write_it():
    actor = user("账务查看者")
    assert frappe.has_permission("Ledger Evidence", "read", user=actor)
    assert not frappe.has_permission("Ledger Evidence", "write", user=actor)


def evidence(name=None, **values):
    from internal_ledger.permissions import _EVIDENCE_WRITE

    doc = frappe.get_doc({"doctype": "Ledger Evidence", "imported_transaction": name or transaction(),
                          **values})
    doc.flags.ledger_evidence_write = _EVIDENCE_WRITE
    return doc.insert()


def attachment(evidence_name, marked=False):
    from internal_ledger.permissions import _EVIDENCE_FILE_WRITE

    doc = frappe.get_doc({"doctype": "File", "file_name": f"evidence-{uuid4().hex}.txt",
        "content": uuid4().hex, "is_private": 1,
        "attached_to_doctype": "Ledger Evidence", "attached_to_name": evidence_name})
    if marked:
        doc.flags.evidence_file_write = _EVIDENCE_FILE_WRITE
    return doc.insert(ignore_permissions=True)


def test_controlled_evidence_derives_company_and_sanitizes_note():
    doc = evidence(note_html='<p>核对说明</p><script>alert(1)</script>')
    saved = frappe.get_doc("Ledger Evidence", doc.name)
    assert saved.company == frappe.flags.classification_company
    assert "核对说明" in saved.note_html
    assert "<script>" not in saved.note_html
    assert evidence(note_html=None).note_html == ""


def test_ledger_admin_can_create_evidence_without_a_stored_company():
    name = transaction()
    actor = user("账套管理员")
    frappe.set_user(actor)
    doc = evidence(name, note_html="管理员核对")
    assert doc.company == frappe.flags.classification_company
    assert doc.owner == actor


def test_transaction_has_at_most_one_evidence_record():
    name = transaction()
    evidence(name)
    with unittest.TestCase().assertRaises(frappe.UniqueValidationError):
        evidence(name)


def test_generic_evidence_writes_cannot_forge_the_service_marker():
    from frappe import client

    original = evidence(note_html="核对说明")
    for actor in (user("账套管理员"), "Administrator"):
        frappe.set_user(actor)
        doc = frappe.get_doc("Ledger Evidence", original.name)
        doc.flags.ledger_evidence_write = True
        doc.note_html = "伪造更新"
        payload = doc.as_dict()
        payload["flags"] = {"ledger_evidence_write": True}
        for operation in (doc.save, lambda: client.save(payload),
                          lambda: frappe.delete_doc("Ledger Evidence", original.name)):
            with unittest.TestCase().assertRaises(frappe.PermissionError):
                operation()


def test_viewer_cannot_use_the_controlled_write_markers():
    from internal_ledger.permissions import _EVIDENCE_WRITE

    original = evidence()
    name = transaction()
    actor = user("账务查看者")
    frappe.set_user(actor)
    original.check_permission("read")
    doc = frappe.get_doc({"doctype": "Ledger Evidence", "imported_transaction": name})
    doc.flags.ledger_evidence_write = _EVIDENCE_WRITE
    with unittest.TestCase().assertRaises(frappe.PermissionError):
        doc.insert(ignore_permissions=True)
    with unittest.TestCase().assertRaises(frappe.PermissionError):
        attachment(original.name, marked=True)


def test_evidence_company_scope_applies_to_reads_and_transaction_validation():
    own = evidence()
    foreign = evidence()
    other = frappe.get_doc({"doctype": "Company", "company_name": "_Test Evidence Other",
        "abbr": "ILEO", "default_currency": "CNY", "country": "China",
        "create_chart_of_accounts_based_on": "Standard Template", "chart_of_accounts": "Standard"}).insert()
    # Stored company must neither grant foreign access nor hide a current-company record.
    frappe.db.set_value("Ledger Evidence", own.name, "company", other.name)
    batch = frappe.db.get_value("Imported Transaction", foreign.imported_transaction, "import_batch")
    frappe.db.set_value("Import Batch", batch, "company", other.name)
    for actor in (user("账套管理员"), user("账务查看者")):
        frappe.set_user(actor)
        assert frappe.has_permission("Ledger Evidence", "read", own.name)
        assert not frappe.has_permission("Ledger Evidence", "read", foreign.name)
        assert frappe.get_list("Ledger Evidence", pluck="name") == [own.name]
    frappe.set_user("Administrator")
    admin = user("账套管理员")
    frappe.set_user(admin)
    from internal_ledger.permissions import _EVIDENCE_WRITE

    doc = frappe.get_doc("Ledger Evidence", foreign.name)
    doc.flags.ledger_evidence_write = _EVIDENCE_WRITE
    with unittest.TestCase().assertRaises(frappe.PermissionError):
        doc.save(ignore_permissions=True)


def test_direct_evidence_file_insert_is_blocked():
    doc = evidence()
    for actor in (user("账套管理员"), "Administrator"):
        frappe.set_user(actor)
        with unittest.TestCase().assertRaises(frappe.PermissionError):
            attachment(doc.name)


def test_rejected_evidence_upload_writes_no_blob_before_rollback():
    name = evidence().name
    file_name = f"evidence-denied-{uuid4().hex}.txt"
    path = Path(frappe.get_site_path("private", "files", file_name))
    doc = frappe.get_doc({"doctype": "File", "file_name": file_name,
        "content": uuid4().hex, "is_private": 1,
        "attached_to_doctype": "Ledger Evidence", "attached_to_name": name})
    with unittest.TestCase().assertRaises(frappe.PermissionError):
        doc.insert(ignore_permissions=True)
    assert not path.exists()
    assert not frappe.db.exists("File", {"file_name": file_name})


def test_ordinary_file_cannot_publish_a_shared_evidence_blob():
    protected = attachment(evidence().name, marked=True)
    path = Path(protected.get_full_path())
    original = path.read_bytes()
    ordinary = frappe.get_doc({"doctype": "File", "file_name": f"ordinary-{uuid4().hex}.txt",
        "content": original, "is_private": 1, "attached_to_doctype": "Company",
        "attached_to_name": frappe.flags.classification_company}).insert(ignore_permissions=True)
    assert ordinary.content_hash == protected.content_hash
    assert ordinary.file_url == protected.file_url
    for is_private in (0, "0"):
        ordinary = frappe.get_doc("File", ordinary.name)
        ordinary.is_private = is_private
        with unittest.TestCase().assertRaises(frappe.PermissionError):
            ordinary.save(ignore_permissions=True)
        saved = frappe.get_doc("File", protected.name)
        assert saved.is_private == 1 and saved.file_url == protected.file_url
        assert path.read_bytes() == original
    # File payload fields cannot bypass protection of the actual shared blob path.
    ordinary = frappe.get_doc("File", ordinary.name)
    ordinary.is_private = 0
    ordinary.content_hash = "forged"
    with unittest.TestCase().assertRaises(frappe.PermissionError):
        ordinary.save(ignore_permissions=True)
    ordinary.is_private = 1
    ordinary.file_name = path.name
    with unittest.TestCase().assertRaises(frappe.PermissionError):
        ordinary.save_file(content=b"forged", overwrite=True)
    saved = frappe.get_doc("File", protected.name)
    assert saved.is_private == 1 and saved.file_url == protected.file_url
    assert path.read_bytes() == original


def test_evidence_file_changes_require_private_controlled_writes():
    from internal_ledger.permissions import _EVIDENCE_FILE_WRITE

    attached = attachment(evidence().name, marked=True)
    for changes in ({"file_name": "replaced.txt"},
                    {"attached_to_doctype": None, "attached_to_name": None}):
        doc = frappe.get_doc("File", attached.name)
        doc.update(changes)
        doc.flags.evidence_file_write = True
        with unittest.TestCase().assertRaises(frappe.PermissionError):
            doc.save(ignore_permissions=True)
    doc = frappe.get_doc("File", attached.name)
    doc.flags.evidence_file_write = _EVIDENCE_FILE_WRITE
    doc.is_private = 0
    with unittest.TestCase().assertRaises(frappe.PermissionError):
        doc.save(ignore_permissions=True)
    doc = frappe.get_doc("File", attached.name)
    doc.flags.evidence_file_write = _EVIDENCE_FILE_WRITE
    doc.file_name = "controlled.txt"
    doc.save(ignore_permissions=True)
    doc.delete(ignore_permissions=True)
    assert not frappe.db.exists("File", attached.name)


def test_rejected_evidence_file_delete_preserves_the_blob_for_administrator():
    name = evidence().name
    for actor in (user("账套管理员"), "Administrator"):
        frappe.set_user("Administrator")
        attached = attachment(name, marked=True)
        path = Path(attached.get_full_path())
        original = path.read_bytes()
        frappe.set_user(actor)
        with unittest.TestCase().assertRaises(frappe.PermissionError):
            frappe.delete_doc("File", attached.name)
        assert path.read_bytes() == original
        assert frappe.db.exists("File", attached.name)


def test_rejected_source_file_delete_preserves_the_blob():
    from internal_ledger.permissions import _SOURCE_INSERT

    name = transaction()
    source = frappe.get_doc({"doctype": "Internal Ledger Source File",
        "import_batch": frappe.db.get_value("Imported Transaction", name, "import_batch")})
    source.flags.source_insert = _SOURCE_INSERT
    source.insert()
    attached = frappe.get_doc({"doctype": "File", "file_name": f"source-{uuid4().hex}.txt",
        "content": uuid4().hex, "is_private": 1,
        "attached_to_doctype": source.doctype, "attached_to_name": source.name})
    attached.flags.source_insert = _SOURCE_INSERT
    attached.insert(ignore_permissions=True)
    path = Path(attached.get_full_path())
    original = path.read_bytes()
    with unittest.TestCase().assertRaises(frappe.PermissionError):
        attached.delete(ignore_permissions=True)
    assert path.read_bytes() == original
    assert frappe.db.exists("File", attached.name)


def test_unrelated_file_can_still_be_deleted():
    doc = frappe.get_doc({"doctype": "File", "file_name": f"unrelated-{uuid4().hex}.txt",
                          "content": uuid4().hex, "is_private": 1}).insert(ignore_permissions=True)
    path = Path(doc.get_full_path())
    doc.delete(ignore_permissions=True)
    assert not frappe.db.exists("File", doc.name)
    assert not path.exists()


def test_unrelated_file_can_still_change_privacy():
    content = uuid4().hex.encode()
    doc = frappe.get_doc({"doctype": "File", "file_name": f"privacy-{uuid4().hex}.txt",
                          "content": content, "is_private": 1}).insert(ignore_permissions=True)
    private_path = Path(doc.get_full_path())
    doc.is_private = 0
    doc.save(ignore_permissions=True)
    assert doc.file_url.startswith("/files/")
    assert Path(doc.get_full_path()).read_bytes() == content
    doc.is_private = 1
    doc.save(ignore_permissions=True)
    assert private_path.read_bytes() == content


def tear_down():
    frappe.set_user("Administrator")
    frappe.db.rollback()
    frappe.clear_cache()


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(
        unittest.FunctionTestCase(test, setUp=set_up, tearDown=tear_down)
        for name, test in sorted(globals().items()) if name.startswith("test_")
    )
