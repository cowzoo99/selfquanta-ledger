import base64
import json
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import frappe

from internal_ledger.tests.test_classifier import set_up, transaction
from internal_ledger.tests.test_permissions import user


def save_note(api, tx, note):
    return api.save_evidence(tx, note, api.get_evidence(tx)["note_version"])


def legacy_file_share(name, actor):
    ordinary = frappe.get_doc({"doctype": "File", "file_name": f"share-{uuid4().hex}.txt",
        "content": uuid4().hex, "is_private": 1}).insert(ignore_permissions=True)
    share = frappe.share.add("File", ordinary.name, actor, read=1, flags={"ignore_share_permission": True})
    frappe.db.set_value("DocShare", share.name, "share_name", name)
    return share.name


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


def test_json_note_payload_is_sanitized_on_save():
    from internal_ledger.api import evidence_api as api

    tx = transaction()
    payload = json.dumps({"note": "<img src=x onerror=alert(1)>"})
    result = save_note(api, tx, payload)
    assert "onerror" not in result["note_html"]
    assert "onerror" not in frappe.db.get_value("Ledger Evidence", result["name"], "note_html")
    table = "<table><tbody><tr><td>核对</td><td>金额</td></tr></tbody></table>"
    assert "<table>" in save_note(api, tx, table)["note_html"]


def test_existing_dirty_json_note_is_sanitized_before_api_read():
    from internal_ledger.api import evidence_api as api

    doc = evidence(note_html="原备注")
    payload = json.dumps({"note": "<img src=x onerror=alert(1)>"})
    frappe.db.set_value("Ledger Evidence", doc.name, "note_html", payload)
    frappe.set_user(user("账务查看者"))
    result = api.get_evidence(doc.imported_transaction)
    assert "onerror" not in result["note_html"]
    assert frappe.db.get_value("Ledger Evidence", doc.name, "note_html") == payload, "reads must not mutate stored notes"


def test_note_whitelist_strips_forms_and_tracking_on_save_and_read():
    from internal_ledger.api import evidence_api as api

    tx = transaction()
    payload = ('<p>说明<strong>加粗</strong></p><ul><li>项目</li></ul>'
               '<table><tr><td>单元格</td></tr></table><a href="https://example.com">安全链接</a>'
               '<a href="javascript:alert(1)">危险链接</a><form action="https://evil.test">'
               '<input type="password"><button>提交</button></form><img src="https://evil.test/track">')
    saved = save_note(api, tx, payload)
    for html in (saved["note_html"],):
        for allowed in ("<p>", "<strong>", "<ul>", "<li>", "<table>", "<tbody>", "<tr>", "<td>", 'href="https://example.com"'):
            assert allowed in html
        for denied in ("<form", "<input", "<button", "<img", "javascript:", "evil.test"):
            assert denied not in html
    frappe.db.set_value("Ledger Evidence", saved["name"], "note_html", json.dumps({"note": payload}))
    html = api.get_evidence(tx)["note_html"]
    for denied in ("<form", "<input", "<button", "<img", "javascript:", "evil.test"):
        assert denied not in html, html


def test_note_save_rejects_stale_versions_including_first_save():
    from internal_ledger.api import evidence_api as api

    tx = transaction()
    alice, bob = user("账套管理员"), user("账套管理员")
    frappe.set_user(alice)
    first = api.get_evidence(tx)
    frappe.set_user(bob)
    second = api.get_evidence(tx)
    frappe.set_user(alice)
    saved = api.save_evidence(tx, "<p>管理员甲</p>", first["note_version"])
    frappe.set_user(bob)
    with unittest.TestCase().assertRaises(frappe.ValidationError):
        api.save_evidence(tx, "<p>管理员乙</p>", second["note_version"])
    assert api.get_evidence(tx)["note_html"] == saved["note_html"]
    old = api.get_evidence(tx)
    frappe.set_user(alice)
    saved = api.save_evidence(tx, "<p>甲再次修改</p>", old["note_version"])
    frappe.set_user(bob)
    with unittest.TestCase().assertRaises(frappe.ValidationError):
        api.save_evidence(tx, "<p>乙的旧版本</p>", old["note_version"])
    assert api.get_evidence(tx)["note_html"] == saved["note_html"]


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


def test_generic_file_cannot_reference_or_delete_protected_blob():
    from frappe import client
    from internal_ledger.api.evidence_api import upload_evidence

    tx = transaction()
    info = upload_evidence(tx, "凭证.txt", encoded(uuid4().hex.encode()), "text/plain")["attachments"][0]
    protected = frappe.get_doc("File", info["name"])
    audit_count = frappe.db.count("Internal Ledger Audit Event", {"reference_name": tx, "action": "upload_evidence"})
    path = Path(protected.get_full_path())
    original = path.read_bytes()
    ordinary = frappe.get_doc({"doctype": "File", "file_name": f"ordinary-{uuid4().hex}.txt",
        "content": uuid4().hex, "is_private": 1}).insert(ignore_permissions=True)
    actor = user("账务查看者")
    frappe.db.set_value("File", ordinary.name, "owner", actor)
    frappe.set_user(actor)
    try:
        for field, target in (("file_url", protected.file_url), ("thumbnail_url", protected.file_url),
                              ("thumbnail_url", "https://example.invalid/" + path.name)):
            previous = frappe.db.get_value("File", ordinary.name, field)
            with unittest.TestCase().assertRaises(frappe.PermissionError):
                client.set_value("File", ordinary.name, field, target)
            assert path.read_bytes() == original
            # Simulate a historical row created before the protection existed.
            frappe.db.set_value("File", ordinary.name, field, target)
            with unittest.TestCase().assertRaises(frappe.PermissionError):
                client.delete("File", ordinary.name)
            assert path.read_bytes() == original
            assert frappe.db.exists("File", protected.name)
            assert frappe.db.count("Internal Ledger Audit Event", {"reference_name": tx, "action": "upload_evidence"}) == audit_count == 1
            frappe.db.set_value("File", ordinary.name, field, previous)
    finally:
        if not path.exists():
            path.write_bytes(original)
        frappe.set_user("Administrator")


def test_source_owner_and_alias_do_not_bypass_current_read_permissions():
    from frappe import client
    from frappe.handler import download_file
    from frappe.utils.response import download_private_file
    from werkzeug.exceptions import Forbidden
    from internal_ledger.permissions import _SOURCE_INSERT

    name = transaction()
    source = frappe.get_doc({"doctype": "Internal Ledger Source File",
        "import_batch": frappe.db.get_value("Imported Transaction", name, "import_batch")})
    source.flags.source_insert = _SOURCE_INSERT
    source.insert()
    protected = frappe.get_doc({"doctype": "File", "file_name": f"source-{uuid4().hex}.txt",
        "content": uuid4().hex, "is_private": 1,
        "attached_to_doctype": source.doctype, "attached_to_name": source.name})
    protected.flags.source_insert = _SOURCE_INSERT
    protected.insert(ignore_permissions=True)
    alias = frappe.get_doc({"doctype": "File", "file_name": f"alias-{uuid4().hex}.txt",
        "content": uuid4().hex, "is_private": 1}).insert(ignore_permissions=True)
    actor = user("账务查看者")
    frappe.db.set_value("File", protected.name, "owner", actor)
    frappe.db.set_value("File", alias.name, {"owner": actor, "file_url": protected.file_url})
    legacy_file_share(protected.name, actor)
    legacy_file_share(alias.name, actor)
    frappe.set_user(actor)
    for file in (protected, alias):
        with unittest.TestCase().assertRaises(frappe.PermissionError):
            client.get("File", file.name)
        with unittest.TestCase().assertRaises(frappe.PermissionError):
            frappe.has_permission("File", "read", file.name)
    with unittest.TestCase().assertRaises(frappe.PermissionError):
        download_file(protected.file_url)
    with unittest.TestCase().assertRaises(Forbidden):
        download_private_file(protected.file_url)
    frappe.set_user("Administrator")
    from internal_ledger.permissions import cleanup_ledger_file_shares
    cleanup_ledger_file_shares()
    frappe.set_user(actor)
    assert not frappe.get_list("File", filters={"name": ["in", [protected.name, alias.name]]}, pluck="name")
    assert not client.get_value("File", "file_url", protected.name)


def test_batch_zip_obeys_current_file_permissions_for_owner_share_and_alias():
    from io import BytesIO
    from zipfile import ZipFile
    from frappe.handler import execute_cmd
    from internal_ledger.api.evidence_api import upload_evidence
    from internal_ledger.permissions import _SOURCE_INSERT

    tx = transaction()
    source = frappe.get_doc({"doctype": "Internal Ledger Source File",
        "import_batch": frappe.db.get_value("Imported Transaction", tx, "import_batch")})
    source.flags.source_insert = _SOURCE_INSERT
    source.insert()
    protected = frappe.get_doc({"doctype": "File", "file_name": f"source-{uuid4().hex}.txt",
        "content": uuid4().hex, "is_private": 1, "attached_to_doctype": source.doctype,
        "attached_to_name": source.name})
    protected.flags.source_insert = _SOURCE_INSERT
    protected.insert(ignore_permissions=True)
    content = uuid4().hex.encode()
    info = upload_evidence(tx, "佐证.txt", encoded(content), "text/plain")["attachments"][0]
    alias = frappe.get_doc({"doctype": "File", "file_name": f"alias-{uuid4().hex}.txt",
        "content": uuid4().hex, "is_private": 1}).insert(ignore_permissions=True)
    viewer = user("账务查看者")
    frappe.db.set_value("File", protected.name, "owner", viewer)
    frappe.db.set_value("File", alias.name, {"owner": viewer, "file_url": protected.file_url})
    for doc in (protected, alias):
        legacy_file_share(doc.name, viewer)
    frappe.set_user(viewer)
    original_form, original_request = frappe.local.form_dict, getattr(frappe.local, "request", None)
    try:
        frappe.local.request = frappe._dict(method="POST")
        for name in (protected.name, alias.name):
            frappe.local.form_dict = frappe._dict(files=json.dumps([name]))
            for endpoint in ("frappe.core.api.file.zip_files", "frappe.core.doctype.file.file.zip_files"):
                with unittest.TestCase().assertRaises(frappe.PermissionError):
                    execute_cmd(endpoint)
            with unittest.TestCase().assertRaises(frappe.PermissionError):
                frappe.get_doc("File", name).get_content()
        frappe.local.form_dict = frappe._dict(files=json.dumps([info["name"]]))
        execute_cmd("frappe.core.api.file.zip_files")
        with ZipFile(BytesIO(frappe.response["filecontent"])) as archive:
            assert archive.read("佐证.txt") == content
        other = frappe.db.get_single_value("Internal Ledger Settings", "company")
        frappe.db.set_single_value("Internal Ledger Settings", "company", "其他公司")
        try:
            with unittest.TestCase().assertRaises(frappe.PermissionError):
                execute_cmd("frappe.core.api.file.zip_files")
        finally:
            frappe.db.set_single_value("Internal Ledger Settings", "company", other)
    finally:
        frappe.local.form_dict, frappe.local.request = original_form, original_request
        frappe.set_user("Administrator")


def test_historical_url_alias_uses_physical_file_identity():
    from frappe import client
    from internal_ledger.permissions import _SOURCE_INSERT, cleanup_ledger_file_shares

    tx = transaction()
    source = frappe.get_doc({"doctype": "Internal Ledger Source File",
        "import_batch": frappe.db.get_value("Imported Transaction", tx, "import_batch")})
    source.flags.source_insert = _SOURCE_INSERT
    source.insert()
    protected = frappe.get_doc({"doctype": "File", "file_name": f"source-{uuid4().hex}.txt",
        "content": uuid4().hex, "is_private": 1, "attached_to_doctype": source.doctype,
        "attached_to_name": source.name})
    protected.flags.source_insert = _SOURCE_INSERT
    protected.insert(ignore_permissions=True)
    content = Path(protected.get_full_path()).read_bytes()
    viewer = user("账务查看者")
    alias = frappe.get_doc({"doctype": "File", "file_name": f"alias-{uuid4().hex}.txt",
        "content": uuid4().hex, "is_private": 1}).insert(ignore_permissions=True)
    altered = protected.file_url.replace("/private/files/", "/private/files//")
    frappe.db.set_value("File", alias.name, {"file_url": altered, "owner": viewer})
    share = legacy_file_share(alias.name, viewer)
    from frappe.utils import get_url
    for altered in (altered, get_url() + protected.file_url):
        frappe.db.set_value("File", alias.name, "file_url", altered)
        frappe.set_user(viewer)
        with unittest.TestCase().assertRaises(frappe.PermissionError):
            client.get("File", alias.name)
        with unittest.TestCase().assertRaises(frappe.PermissionError):
            frappe.get_doc("File", alias.name).get_content()
        with unittest.TestCase().assertRaises(frappe.PermissionError):
            client.delete("File", alias.name)
        frappe.set_user("Administrator")
    with unittest.TestCase().assertRaises(frappe.PermissionError):
        client.set_value("File", alias.name, "file_url", "/private/files/../files/" + Path(protected.file_url).name)
    frappe.set_user("Administrator")
    # A distinct case-variant filename on Linux must retain its ordinary share.
    other_name = Path(protected.file_url).name.swapcase()
    other = frappe.get_doc({"doctype": "File", "file_name": other_name,
        "content": uuid4().hex, "is_private": 1}).insert(ignore_permissions=True)
    valid = frappe.share.add("File", other.name, viewer, read=1, flags={"ignore_share_permission": True})
    with unittest.TestCase().assertRaises(frappe.PermissionError):
        frappe.share.add("File", alias.name, user("账务查看者"), read=1, flags={"ignore_share_permission": True})
    cleanup_ledger_file_shares()
    cleanup_ledger_file_shares()
    assert not frappe.db.exists("DocShare", share)
    assert frappe.db.exists("DocShare", valid.name)
    assert Path(protected.get_full_path()).read_bytes() == content
    frappe.set_user(viewer)
    names = frappe.get_list("File", filters={"name": ["in", [alias.name, other.name]]}, pluck="name")
    assert alias.name not in names and other.name in names


def test_batch_zip_preserves_png_bytes_and_text_for_admin_and_viewer():
    from io import BytesIO
    from zipfile import ZipFile
    from frappe.handler import execute_cmd
    from internal_ledger.api.evidence_api import upload_evidence

    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII=")
    files = {"回单.png": (png, "image/png"), "说明.txt": ("中文备注".encode(), "text/plain"),
             "清单.csv": ("项目,金额\n测试,12\n".encode(), "text/csv")}
    tx = transaction()
    for name, (content, mime) in files.items():
        result = upload_evidence(tx, name, encoded(content), mime)
    names = [row["name"] for row in result["attachments"]]
    viewer = user("账务查看者")
    original_form, original_request = frappe.local.form_dict, getattr(frappe.local, "request", None)
    try:
        frappe.local.request = frappe._dict(method="POST")
        for actor in ("Administrator", viewer):
            frappe.set_user(actor)
            for endpoint in ("frappe.core.api.file.zip_files", "frappe.core.doctype.file.file.zip_files"):
                frappe.local.form_dict = frappe._dict(files=json.dumps(names))
                execute_cmd(endpoint)
                with ZipFile(BytesIO(frappe.response["filecontent"])) as archive:
                    for name, (content, _) in files.items():
                        assert archive.read(name) == content, (actor, endpoint, name)
        text_file = next(row for row in result["attachments"] if row["file_name"] == "说明.txt")
        assert frappe.get_doc("File", text_file["name"]).get_content() == "中文备注"
        assert frappe.get_doc("File", text_file["name"]).get_content(encodings=[]) == files["说明.txt"][0]
    finally:
        frappe.local.form_dict, frappe.local.request = original_form, original_request
        frappe.set_user("Administrator")


def test_controlled_file_and_alias_cannot_be_shared():
    from frappe import client

    protected = attachment(evidence().name, marked=True)
    alias = frappe.get_doc({"doctype": "File", "file_name": f"alias-{uuid4().hex}.txt",
        "content": Path(protected.get_full_path()).read_bytes(), "is_private": 1}).insert(ignore_permissions=True)
    assert alias.file_url == protected.file_url
    viewer = user("账务查看者")
    for file in (protected, alias):
        with unittest.TestCase().assertRaises(frappe.PermissionError):
            frappe.share.add("File", file.name, viewer, read=1, flags={"ignore_share_permission": True})
    ordinary = frappe.get_doc({"doctype": "File", "file_name": f"shareable-{uuid4().hex}.txt",
        "content": uuid4().hex, "is_private": 1}).insert(ignore_permissions=True)
    share = frappe.share.add("File", ordinary.name, viewer, read=1, flags={"ignore_share_permission": True})
    with unittest.TestCase().assertRaisesRegex(frappe.PermissionError, "不能单独分享"):
        client.set_value("DocShare", share.name, "share_name", protected.name)


def test_native_deduplicated_alias_can_be_deleted_without_losing_evidence():
    from frappe import client
    from frappe.handler import download_file
    from internal_ledger.api.evidence_api import upload_evidence

    content = uuid4().hex.encode()
    info = upload_evidence(transaction(), "佐证.txt", encoded(content), "text/plain")["attachments"][0]
    protected = frappe.get_doc("File", info["name"])
    viewer = user("账务查看者")
    frappe.set_user(viewer)
    alias = frappe.get_doc({"doctype": "File", "file_name": f"own-{uuid4().hex}.txt",
        "content": content, "is_private": 1}).insert()
    assert alias.file_url == protected.file_url and alias.content_hash == protected.content_hash
    client.delete("File", alias.name)
    assert not frappe.db.exists("File", alias.name)
    assert frappe.db.exists("File", protected.name)
    assert Path(protected.get_full_path()).read_bytes() == content
    download_file(info["file_url"])
    assert frappe.local.response.filecontent.encode() == content


def test_unauthorized_deduplicated_source_alias_cannot_be_deleted():
    from frappe import client
    from internal_ledger.permissions import _SOURCE_INSERT

    tx = transaction()
    source = frappe.get_doc({"doctype": "Internal Ledger Source File",
        "import_batch": frappe.db.get_value("Imported Transaction", tx, "import_batch")})
    source.flags.source_insert = _SOURCE_INSERT
    source.insert()
    protected = frappe.get_doc({"doctype": "File", "file_name": f"source-{uuid4().hex}.txt",
        "content": uuid4().hex, "is_private": 1, "attached_to_doctype": source.doctype,
        "attached_to_name": source.name})
    protected.flags.source_insert = _SOURCE_INSERT
    protected.insert(ignore_permissions=True)
    content = Path(protected.get_full_path()).read_bytes()
    alias = frappe.get_doc({"doctype": "File", "file_name": f"alias-{uuid4().hex}.txt",
        "content": content, "is_private": 1}).insert(ignore_permissions=True)
    viewer = user("账务查看者")
    frappe.db.set_value("File", alias.name, "owner", viewer)
    frappe.set_user(viewer)
    with unittest.TestCase().assertRaises(frappe.PermissionError):
        client.delete("File", alias.name)
    assert frappe.db.exists("File", alias.name)
    assert Path(protected.get_full_path()).read_bytes() == content


def test_migrate_removes_only_legacy_protected_file_shares():
    from internal_ledger.permissions import _SOURCE_INSERT

    tx = transaction()
    source = frappe.get_doc({"doctype": "Internal Ledger Source File",
        "import_batch": frappe.db.get_value("Imported Transaction", tx, "import_batch")})
    source.flags.source_insert = _SOURCE_INSERT
    source.insert()
    protected = frappe.get_doc({"doctype": "File", "file_name": f"source-{uuid4().hex}.txt",
        "content": uuid4().hex, "is_private": 1, "attached_to_doctype": source.doctype,
        "attached_to_name": source.name})
    protected.flags.source_insert = _SOURCE_INSERT
    protected.insert(ignore_permissions=True)
    alias = frappe.get_doc({"doctype": "File", "file_name": f"alias-{uuid4().hex}.txt",
        "content": Path(protected.get_full_path()).read_bytes(), "is_private": 1}).insert(ignore_permissions=True)
    viewer = user("账务查看者")
    stale = [legacy_file_share(doc.name, viewer) for doc in (protected, alias)]
    ordinary = frappe.get_doc({"doctype": "File", "file_name": f"own-{uuid4().hex}.txt",
        "content": uuid4().hex, "is_private": 1}).insert(ignore_permissions=True)
    frappe.db.set_value("File", ordinary.name, "owner", viewer)
    valid = frappe.share.add("File", ordinary.name, viewer, read=1, flags={"ignore_share_permission": True})
    hook = "internal_ledger.permissions.cleanup_ledger_file_shares"
    assert hook in frappe.get_hooks("after_migrate")
    frappe.get_attr(hook)()
    frappe.get_attr(hook)()
    assert all(not frappe.db.exists("DocShare", name) for name in stale)
    assert frappe.db.exists("DocShare", valid.name)
    frappe.set_user(viewer)
    names = frappe.get_list("File", filters={"name": ["in", [ordinary.name, protected.name, alias.name]]}, pluck="name")
    assert ordinary.name in names
    assert not set(names) & {protected.name, alias.name}


def test_ordinary_privacy_conversion_cannot_rebind_protected_same_hash():
    from frappe import client
    from internal_ledger.api.evidence_api import upload_evidence
    from internal_ledger.permissions import _SOURCE_INSERT

    content = uuid4().hex.encode()
    public = frappe.get_doc({"doctype": "File", "file_name": f"public-{uuid4().hex}.txt",
        "content": content, "is_private": 0}).insert(ignore_permissions=True)
    tx = transaction()
    info = upload_evidence(tx, "凭证.txt", encoded(content), "text/plain")["attachments"][0]
    protected = frappe.get_doc("File", info["name"])
    source = frappe.get_doc({"doctype": "Internal Ledger Source File",
        "import_batch": frappe.db.get_value("Imported Transaction", tx, "import_batch")})
    source.flags.source_insert = _SOURCE_INSERT
    source.insert()
    original = frappe.get_doc({"doctype": "File", "file_name": f"source-{uuid4().hex}.txt",
        "content": content, "is_private": 1, "attached_to_doctype": source.doctype,
        "attached_to_name": source.name})
    original.flags.source_insert = _SOURCE_INSERT
    original.insert(ignore_permissions=True)
    frappe.db.set_value(source.doctype, source.name, "raw_file", original.file_url)
    public_path, protected_path = Path(public.get_full_path()), Path(protected.get_full_path())
    with unittest.TestCase().assertRaises(frappe.PermissionError):
        client.set_value("File", public.name, "is_private", 1)
    assert frappe.db.get_value("File", protected.name, "file_url") == info["file_url"]
    assert frappe.db.get_value("File", original.name, "file_url") == original.file_url
    assert frappe.db.get_value(source.doctype, source.name, "raw_file") == original.file_url
    assert public_path.read_bytes() == protected_path.read_bytes() == content
    assert Path(original.get_full_path()).read_bytes() == content


def test_new_public_same_hash_does_not_rebind_existing_evidence():
    from internal_ledger.api.evidence_api import upload_evidence

    content = uuid4().hex.encode()
    info = upload_evidence(transaction(), "凭证.txt", encoded(content), "text/plain")["attachments"][0]
    protected = frappe.get_doc("File", info["name"])
    path = Path(protected.get_full_path())
    public = frappe.get_doc({"doctype": "File", "file_name": f"public-{uuid4().hex}.txt",
        "content": content, "is_private": 0}).insert(ignore_permissions=True)
    assert frappe.db.get_value("File", protected.name, ["file_url", "is_private"]) == (info["file_url"], 1)
    assert path.read_bytes() == Path(public.get_full_path()).read_bytes() == content


def test_evidence_owner_cannot_download_after_company_changes():
    from frappe import client
    from frappe.handler import download_file
    from internal_ledger.api.evidence_api import upload_evidence

    content = uuid4().hex.encode()
    tx = transaction()
    info = upload_evidence(tx, "凭证.txt", encoded(content), "text/plain")["attachments"][0]
    actor = user("账务查看者")
    frappe.db.set_value("File", info["name"], "owner", actor)
    frappe.set_user(actor)
    download_file(info["file_url"])
    assert frappe.local.response.filecontent.encode() == content
    company = frappe.db.get_single_value("Internal Ledger Settings", "company")
    frappe.db.set_single_value("Internal Ledger Settings", "company", "其他公司")
    try:
        with unittest.TestCase().assertRaises(frappe.PermissionError):
            client.get("File", info["name"])
        with unittest.TestCase().assertRaises(frappe.PermissionError):
            download_file(info["file_url"])
        assert not frappe.get_list("File", filters={"name": info["name"]}, pluck="name")
        assert Path(frappe.get_doc("File", info["name"]).get_full_path()).read_bytes() == content
    finally:
        frappe.db.set_single_value("Internal Ledger Settings", "company", company)
        frappe.set_user("Administrator")


def test_foreign_company_evidence_api_is_denied_even_for_administrator():
    from internal_ledger.api import evidence_api as api
    from internal_ledger.api.review_api import list_review_items

    tx = transaction()
    saved = save_note(api, tx, "原备注")
    info = api.upload_evidence(tx, "佐证.txt", encoded(b"protected bytes"), "text/plain")["attachments"][0]
    file = frappe.get_doc("File", info["name"])
    path = Path(file.get_full_path())
    audit_count = frappe.db.count("Internal Ledger Audit Event", {"reference_name": tx})
    batch = frappe.db.get_value("Imported Transaction", tx, "import_batch")
    frappe.db.set_value("Import Batch", batch, "company", "其他公司")
    for actor in ("Administrator", user("账套管理员"), user("账务查看者")):
        frappe.set_user(actor)
        for operation in (lambda: api.get_evidence(tx),
                          lambda: api.save_evidence(tx, "非法修改", saved["note_version"]),
                          lambda: api.upload_evidence(tx, "x.txt", encoded(b"x"), "text/plain"),
                          lambda: api.delete_evidence_file(tx, info["name"])):
            with unittest.TestCase().assertRaises(frappe.PermissionError):
                operation()
        assert tx not in [row["transaction"]["name"] for row in list_review_items()]
    assert frappe.db.get_value("Ledger Evidence", saved["name"], "note_html") == "原备注"
    assert path.read_bytes() == b"protected bytes"
    assert frappe.db.exists("File", info["name"])
    assert frappe.db.count("Internal Ledger Audit Event", {"reference_name": tx}) == audit_count


def test_native_mt940_conversion_checks_source_file_access():
    from frappe.handler import execute_cmd
    from internal_ledger.permissions import _SOURCE_INSERT

    mt940 = (":20:STARTUMSE\n:25:12345678901234567890\n:28C:1/1\n:60F:C031002EUR0,00\n"
             ":61:0310021002DR123,45NMSCNONREF//8327000090031789\n:86:Test\n:62F:C031002EUR0,00\n-")
    tx = transaction()
    source = frappe.get_doc({"doctype": "Internal Ledger Source File",
        "import_batch": frappe.db.get_value("Imported Transaction", tx, "import_batch")})
    source.flags.source_insert = _SOURCE_INSERT
    source.insert()
    protected = frappe.get_doc({"doctype": "File", "file_name": f"source-{uuid4().hex}.txt",
        "content": mt940, "is_private": 1, "attached_to_doctype": source.doctype,
        "attached_to_name": source.name})
    protected.flags.source_insert = _SOURCE_INSERT
    protected.insert(ignore_permissions=True)
    ordinary = frappe.get_doc({"doctype": "File", "file_name": f"ordinary-{uuid4().hex}.txt",
        "content": mt940 + "\n", "is_private": 1}).insert(ignore_permissions=True)
    docname = f"test-bank-import-{uuid4().hex}"
    frappe.db.sql("INSERT INTO `tabBank Statement Import` (name, owner, import_mt940_fromat, bank_account) VALUES (%s, %s, 1, %s)",
                  (docname, "Administrator", "test-bank"))
    manager = user("System Manager")
    admin = user("账套管理员")
    admin_doc = frappe.get_doc("User", admin)
    admin_doc.append("roles", {"role": "System Manager"})
    admin_doc.save()
    original_form, original_request = frappe.local.form_dict, getattr(frappe.local, "request", None)
    try:
        frappe.local.request = frappe._dict(method="POST")
        endpoint = "erpnext.accounts.doctype.bank_statement_import.bank_statement_import.convert_mt940_to_csv"
        frappe.set_user(manager)
        frappe.local.form_dict = frappe._dict(data_import=docname, mt940_file_path=protected.file_url)
        with unittest.TestCase().assertRaises(frappe.PermissionError):
            execute_cmd(endpoint)
        for actor, file in ((manager, ordinary), (admin, protected)):
            frappe.set_user(actor)
            frappe.local.form_dict = frappe._dict(data_import=docname, mt940_file_path=file.file_url)
            result = execute_cmd(endpoint)
            assert result.startswith("/private/files/")
            converted = frappe.get_doc("File", {"file_url": result})
            assert b"Deposit" in Path(converted.get_full_path()).read_bytes()
    finally:
        frappe.local.form_dict, frappe.local.request = original_form, original_request
        frappe.set_user("Administrator")


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
    empty = api.get_evidence(tx)
    assert empty == {"name": None, "transaction": tx, "note_html": "", "note_version": empty["note_version"], "attachments": []}
    assert not frappe.db.exists("Ledger Evidence", {"imported_transaction": tx})
    saved = save_note(api, tx, '<p>业务说明</p><script>alert(1)</script><table><tr><td>合同</td></tr></table>')
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
    save_note(api, other, "")
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
    for operation in (lambda: save_note(api, tx, "越权"),
                      lambda: api.upload_evidence(tx, "禁止.txt", encoded(b"x"), "text/plain"),
                      lambda: api.delete_evidence_file(tx, info["name"])):
        with unittest.TestCase().assertRaises(frappe.PermissionError):
            operation()


def test_audit_failure_preserves_note_attachment_and_blob():
    from internal_ledger.api import evidence_api as api

    tx = transaction()
    filename = f"failed-{uuid4().hex}.txt"
    with patch.object(api, "record_audit", side_effect=RuntimeError("audit unavailable")):
        for operation in (lambda: save_note(api, tx, "不能无审计保存"),
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
    saved = save_note(api, tx, "原备注")
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


def test_deleting_public_same_hash_removes_public_blob_but_keeps_evidence():
    from frappe import client
    from frappe.handler import download_file
    from internal_ledger.api.evidence_api import upload_evidence

    content = uuid4().hex.encode()
    public = frappe.get_doc({"doctype": "File", "file_name": f"pub-{uuid4().hex}.txt",
        "content": content, "is_private": 0}).insert()
    public_path = Path(public.get_full_path())
    info = upload_evidence(transaction(), "佐证.txt", encoded(content), "text/plain")["attachments"][0]
    client.delete("File", public.name)
    assert not public_path.exists()
    download_file(info["file_url"])
    assert frappe.local.response.filecontent.encode() == content


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
