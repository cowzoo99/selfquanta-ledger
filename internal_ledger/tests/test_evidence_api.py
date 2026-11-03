import base64
import unittest
from pathlib import Path
from unittest.mock import patch
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


def encoded(content):
    return base64.b64encode(content).decode("ascii")


def private_blobs(filename):
    return set(Path(frappe.get_site_path("private", "files")).glob("*" + filename))


def test_long_unicode_display_name_survives_generated_storage_name():
    from internal_ledger.api.evidence_api import upload_evidence

    filename = "凭据" * 40 + ".txt"
    content = uuid4().hex.encode()
    info, = upload_evidence(transaction(), filename, encoded(content), "text/plain")["attachments"]
    physical = Path(frappe.get_doc("File", info["name"]).get_full_path())
    assert info["file_name"] == filename and physical.name.startswith("evidence-")
    assert len(physical.name.encode()) <= 255 and physical.name.endswith("凭据.txt")
    assert physical.read_bytes() == content


def test_admin_saves_sanitized_note_private_attachment_and_audit():
    from internal_ledger.api import evidence_api as api

    tx = transaction()
    actor = user("账套管理员")
    frappe.set_user(actor)
    assert api.get_evidence(tx) == {"name": None, "transaction": tx, "note_html": "", "attachments": []}
    assert not frappe.db.exists("Ledger Evidence", {"imported_transaction": tx})
    saved = api.save_evidence(tx, '<p>业务说明</p><script>alert(1)</script><table><tr><td>合同</td></tr></table>')
    assert "业务说明" in saved["note_html"] and "<table" in saved["note_html"]
    assert "script" not in saved["note_html"].lower()
    content = b"\x89PNG\r\n\x1a\n" + uuid4().bytes
    result = api.upload_evidence(tx, "回单.png", encoded(content), "image/png")
    info, = result["attachments"]
    doc = frappe.get_doc("File", info["name"])
    assert doc.is_private and doc.file_url.startswith("/private/files/")
    assert (doc.attached_to_doctype, doc.attached_to_name) == ("Ledger Evidence", saved["name"])
    assert Path(doc.get_full_path()).read_bytes() == content
    assert info == {"name": doc.name, "file_name": "回单.png", "file_url": doc.file_url,
                    "file_size": len(content), "extension": ".png", "is_image": True}
    assert api.get_evidence(tx) == result
    assert api.delete_evidence_file(tx, doc.name) == saved
    assert not frappe.db.exists("File", doc.name)
    assert not Path(doc.get_full_path()).exists()
    actions = frappe.get_all("Internal Ledger Audit Event", filters={
        "reference_doctype": "Imported Transaction", "reference_name": tx, "actor": actor,
        "result": "success"}, pluck="action")
    assert sorted(actions) == ["delete_evidence", "update_evidence_note", "upload_evidence"]


def test_upload_rejects_invalid_encoding_size_mime_and_signatures():
    from internal_ledger.api.evidence_api import upload_evidence

    tx = transaction()
    for filename, payload, mime, reason in (
        ("过大.pdf", encoded(b"%PDF-" + b"x" * (10 * 1024 * 1024 - 4)), "application/pdf", "10 MiB"),
        ("文档.pdf", "%%%!!", "application/pdf", "Base64"),
        ("文档.pdf", encoded(b"%PDF-"), "image/png", "MIME"),
        ("脚本.html", encoded(b"<script/>"), "text/html", "不支持"),
        ("文档.pdf", encoded(b"document"), "application/pdf", "文件内容"),
        ("图片.png", encoded(b"PNG"), "image/png", "文件内容"),
        ("图片.jpg", encoded(b"JPEG"), "image/jpeg", "文件内容"),
        ("图片.webp", encoded(b"RIFF0000WAVE"), "image/webp", "文件内容"),
        ("文档.doc", encoded(b"DOC"), "application/msword", "文件内容"),
        ("表.xls", encoded(b"XLS"), "application/vnd.ms-excel", "文件内容"),
        ("文档.docx", encoded(b"PK"), "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "文件内容"),
        ("表.xlsx", encoded(b"PK"), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "文件内容"),
        ("表.csv", encoded(b"a\x00b"), "text/csv", "文件内容"),
        ("备注.txt", encoded(b"a\x00b"), "text/plain", "文件内容"),
    ):
        with unittest.TestCase().assertRaisesRegex(frappe.ValidationError, reason):
            upload_evidence(tx, filename, payload, mime)
    assert not frappe.db.exists("Ledger Evidence", {"imported_transaction": tx})


def test_attachment_count_limit_and_transaction_ownership():
    from internal_ledger.api import evidence_api as api

    tx, other = transaction(), transaction()
    api.save_evidence(other, "")
    for index in range(10):
        files = api.upload_evidence(tx, f"{index}.txt", encoded(uuid4().bytes.hex().encode()), "text/plain")
    assert len(files["attachments"]) == 10
    with unittest.TestCase().assertRaises(frappe.ValidationError):
        api.upload_evidence(tx, "overflow.txt", encoded(b"11"), "text/plain")
    with unittest.TestCase().assertRaises(frappe.PermissionError):
        api.delete_evidence_file(other, files["attachments"][0]["name"])
    assert api.get_evidence(tx)["attachments"] == files["attachments"]


def test_viewer_reads_private_evidence_but_cannot_mutate_it():
    from internal_ledger.api import evidence_api as api
    from frappe.handler import download_file

    tx = transaction()
    content = uuid4().hex
    result = api.upload_evidence(tx, "说明.txt", encoded(content.encode()), "text/plain")
    info, = result["attachments"]
    frappe.set_user(user("账务查看者"))
    assert api.get_evidence(tx) == result
    assert frappe.has_permission("File", "read", info["name"])
    download_file(info["file_url"])
    assert frappe.local.response.filecontent == content
    for operation in (lambda: api.save_evidence(tx, "越权"),
                      lambda: api.upload_evidence(tx, "禁止.txt", encoded(b"x"), "text/plain"),
                      lambda: api.delete_evidence_file(tx, info["name"])):
        with unittest.TestCase().assertRaises(frappe.PermissionError):
            operation()


def test_audit_failure_preserves_note_attachment_and_blob():
    from internal_ledger.api import evidence_api as api

    tx = transaction()
    filename = f"failed-{uuid4().hex}.txt"
    with patch.object(api, "record_audit", side_effect=RuntimeError("audit unavailable")):
        for operation in (lambda: api.save_evidence(tx, "不能无审计保存"),
                          lambda: api.upload_evidence(tx, filename, encoded(uuid4().hex.encode()), "text/plain")):
            with unittest.TestCase().assertRaisesRegex(RuntimeError, "audit unavailable"):
                operation()
            assert not frappe.db.exists("Ledger Evidence", {"imported_transaction": tx})
        assert not private_blobs(filename)
    content = uuid4().hex.encode()
    saved = api.upload_evidence(tx, filename, encoded(content), "text/plain")
    info, = saved["attachments"]
    with patch.object(api, "record_audit", side_effect=RuntimeError("audit unavailable")):
        with unittest.TestCase().assertRaisesRegex(RuntimeError, "audit unavailable"):
            api.delete_evidence_file(tx, info["name"])
    assert api.get_evidence(tx) == saved
    assert Path(frappe.get_doc("File", info["name"]).get_full_path()).read_bytes() == content


def test_failure_after_file_mutation_restores_database_audit_and_blob():
    from internal_ledger.api import evidence_api as api

    tx = transaction()
    saved = api.save_evidence(tx, "原备注")
    filename = f"rollback-{uuid4().hex}.txt"
    content = uuid4().hex.encode()
    with patch.object(api, "_serialize_evidence", side_effect=RuntimeError("response failed")):
        with unittest.TestCase().assertRaisesRegex(RuntimeError, "response failed"):
            api.upload_evidence(tx, filename, encoded(content), "text/plain")
    assert api.get_evidence(tx) == saved
    assert not private_blobs(filename)
    assert not frappe.db.exists("Internal Ledger Audit Event", {"reference_name": tx, "action": "upload_evidence"})
    result = api.upload_evidence(tx, filename, encoded(content), "text/plain")
    with patch.object(api, "_serialize_evidence", side_effect=RuntimeError("response failed")):
        with unittest.TestCase().assertRaisesRegex(RuntimeError, "response failed"):
            api.delete_evidence_file(tx, result["attachments"][0]["name"])
    assert api.get_evidence(tx) == result
    assert Path(frappe.get_doc("File", result["attachments"][0]["name"]).get_full_path()).read_bytes() == content
    assert not frappe.db.exists("Internal Ledger Audit Event", {"reference_name": tx, "action": "delete_evidence"})


def test_invalid_same_name_jpeg_preserves_original_blob():
    from io import BytesIO
    from PIL import Image, UnidentifiedImageError
    from internal_ledger.api import evidence_api as api

    frappe.db.set_single_value("System Settings", "strip_exif_metadata_from_uploaded_images", 1)
    frappe.clear_cache(doctype="System Settings")
    tx = transaction()
    filename = f"proof-{uuid4().hex}.jpg"
    stream = BytesIO()
    Image.new("RGB", (2, 2), "green").save(stream, format="JPEG")
    saved = api.upload_evidence(tx, filename, encoded(stream.getvalue()), "image/jpeg")
    doc = frappe.get_doc("File", saved["attachments"][0]["name"])
    path = Path(doc.get_full_path())
    original = path.read_bytes()
    siblings = private_blobs(filename)
    with unittest.TestCase().assertRaises(UnidentifiedImageError):
        api.upload_evidence(tx, filename, encoded(b"\xff\xd8\xff\x00"), "image/jpeg")
    assert path.read_bytes() == original
    assert private_blobs(filename) == siblings
    assert api.get_evidence(tx) == saved


def test_same_content_same_parent_keeps_both_physical_paths_until_removal():
    from internal_ledger.api import evidence_api as api

    tx = transaction()
    payload = encoded(uuid4().hex.encode())
    filename = f"same-{uuid4().hex}.txt"
    first, = api.upload_evidence(tx, filename, payload, "text/plain")["attachments"]
    saved = api.upload_evidence(tx, filename, payload, "text/plain")
    second, = [row for row in saved["attachments"] if row["name"] != first["name"]]
    assert first["file_url"] != second["file_url"]
    first_path = Path(frappe.get_doc("File", first["name"]).get_full_path())
    second_path = Path(frappe.get_doc("File", second["name"]).get_full_path())
    api.delete_evidence_file(tx, second["name"])
    assert not second_path.exists() and encoded(first_path.read_bytes()) == payload


def test_public_same_content_does_not_keep_deleted_private_blob():
    from internal_ledger.api import evidence_api as api

    tx = transaction()
    content = uuid4().hex.encode()
    public = frappe.get_doc({"doctype": "File", "file_name": f"pub-{uuid4().hex}.txt",
                             "content": content, "is_private": 0}).insert()
    saved = api.upload_evidence(tx, f"priv-{uuid4().hex}.txt", encoded(content), "text/plain")
    doc = frappe.get_doc("File", saved["attachments"][0]["name"])
    assert doc.content_hash == public.content_hash and doc.file_url != public.file_url
    path = Path(doc.get_full_path())
    api.delete_evidence_file(tx, doc.name)
    assert not path.exists()
    assert Path(public.get_full_path()).read_bytes() == content


def test_public_same_content_does_not_keep_rolled_back_private_blob():
    from internal_ledger.api import evidence_api as api

    tx = transaction()
    content = uuid4().hex.encode()
    public = frappe.get_doc({"doctype": "File", "file_name": f"pub-{uuid4().hex}.txt",
                             "content": content, "is_private": 0}).insert()
    filename = f"priv-{uuid4().hex}.txt"
    with patch.object(api, "_serialize_evidence", side_effect=RuntimeError("response failed")):
        with unittest.TestCase().assertRaisesRegex(RuntimeError, "response failed"):
            api.upload_evidence(tx, filename, encoded(content), "text/plain")
    assert not private_blobs(filename)
    assert not frappe.db.exists("File", {"file_name": filename})
    assert Path(public.get_full_path()).read_bytes() == content


def test_failure_during_private_file_write_removes_partial_blob():
    from internal_ledger.api import evidence_api as api

    tx = transaction()
    filename = f"partial-{uuid4().hex}.txt"
    with patch("frappe.core.doctype.file.file.os.fsync", side_effect=OSError("disk failure")):
        with unittest.TestCase().assertRaisesRegex(OSError, "disk failure"):
            api.upload_evidence(tx, filename, encoded(uuid4().hex.encode()), "text/plain")
    assert not private_blobs(filename)
    assert not frappe.db.exists("File", {"file_name": filename})
    assert not frappe.db.exists("Ledger Evidence", {"imported_transaction": tx})


def tear_down():
    frappe.set_user("Administrator")
    frappe.db.rollback()
    frappe.clear_cache()


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(
        unittest.FunctionTestCase(test, setUp=set_up, tearDown=tear_down)
        for name, test in sorted(globals().items()) if name.startswith("test_")
    )
