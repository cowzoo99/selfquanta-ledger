"""Two-connection regression; run only on the isolated test.localhost site."""
from pathlib import Path
from queue import Queue
from threading import Event, Thread
from uuid import uuid4

import frappe

from internal_ledger.permissions import _EVIDENCE_FILE_WRITE


def connect():
    frappe.init(site="test.localhost", sites_path="/opt/internal-ledger-bench/sites")
    frappe.connect()
    frappe.flags.in_test = True
    frappe.set_user("Administrator")


def run(initial_privacy):
    connect()
    assert frappe.local.site == "test.localhost"
    token = uuid4().hex
    batch, tx, evidence = [f"race-{kind}-{token}" for kind in ("batch", "tx", "evidence")]
    company = f"race-company-{token}"
    content = ("race-content-" + token).encode()
    old_company = frappe.db.sql("SELECT value FROM `tabSingles` WHERE doctype=%s AND field=%s",
        ("Internal Ledger Settings", "company"))
    paths, records = [], []
    snapshot, inserted, started, release = (Event() for _ in range(4))
    results = Queue()
    changing = creating = None

    def convert(name):
        connect()
        try:
            frappe.db.sql("SELECT name FROM `tabFile` WHERE name=%s", name)
            snapshot.set()
            assert inserted.wait(20)
            from frappe import client
            started.set()
            try:
                client.set_value("File", name, "is_private", 1 - initial_privacy)
            except frappe.PermissionError:
                results.put("denied")
            else:
                results.put("allowed")
        except Exception as exc:
            results.put(exc)
        finally:
            frappe.db.rollback()
            frappe.destroy()

    def insert_protected():
        connect()
        try:
            doc = frappe.get_doc({"doctype": "File", "file_name": f"race-proof-{token}.txt",
                "content": content, "is_private": 1, "attached_to_doctype": "Ledger Evidence",
                "attached_to_name": evidence})
            doc.flags.evidence_file_write = _EVIDENCE_FILE_WRITE
            doc.insert(ignore_permissions=True)
            records.append(doc.name)
            paths.append(Path(doc.get_full_path()).resolve())
            inserted.set()
            assert release.wait(20)
            frappe.db.commit()
        except Exception as exc:
            results.put(exc)
            inserted.set()
            frappe.db.rollback()
        finally:
            frappe.destroy()

    try:
        frappe.db.set_single_value("Internal Ledger Settings", "company", company)
        frappe.db.sql("INSERT INTO `tabImport Batch` (name, company) VALUES (%s, %s)", (batch, company))
        frappe.db.sql("INSERT INTO `tabImported Transaction` (name, import_batch) VALUES (%s, %s)", (tx, batch))
        frappe.db.sql("INSERT INTO `tabLedger Evidence` (name, imported_transaction, company) VALUES (%s, %s, %s)",
                      (evidence, tx, company))
        public = frappe.get_doc({"doctype": "File", "file_name": f"race-public-{token}.txt",
            "content": content, "is_private": initial_privacy}).insert(ignore_permissions=True)
        records.append(public.name)
        paths.append(Path(public.get_full_path()).resolve())
        frappe.db.commit()

        changing = Thread(target=convert, args=(public.name,))
        changing.start()
        assert snapshot.wait(20)
        creating = Thread(target=insert_protected)
        creating.start()
        assert inserted.wait(20) and started.wait(20)
        changing.join(0.5)
        assert changing.is_alive(), "privacy change did not overlap the held settings lock"
        release.set()
        creating.join(20)
        changing.join(20)
        assert not creating.is_alive() and not changing.is_alive()
        outcome = results.get_nowait()
        assert outcome == "denied", f"privacy conversion must be denied after concurrent commit: {outcome!r}"
        assert len(records) == 2
        protected = frappe.get_doc("File", records[1])
        assert protected.is_private == 1 and protected.file_url.startswith("/private/files/")
        assert Path(protected.get_full_path()).read_bytes() == content
        assert Path(frappe.get_doc("File", public.name).get_full_path()).read_bytes() == content
        from frappe.handler import download_file
        download_file(protected.file_url)
        assert frappe.local.response.filecontent.encode() == content
        frappe.set_user("Guest")
        try:
            try:
                download_file(protected.file_url)
            except frappe.PermissionError:
                pass
            else:
                raise AssertionError("Guest unexpectedly downloaded protected content")
        finally:
            frappe.set_user("Administrator")
        print(f"PASS {initial_privacy}->{1 - initial_privacy}: two connections, old snapshot, overlapping lock, protected bytes and download")
    finally:
        release.set()
        for thread in (creating, changing):
            if thread and thread.is_alive():
                thread.join(20)
        for name in records:
            frappe.db.delete("File", {"name": name})
        frappe.db.delete("Ledger Evidence", {"name": evidence})
        frappe.db.delete("Imported Transaction", {"name": tx})
        frappe.db.delete("Import Batch", {"name": batch})
        if old_company:
            frappe.db.set_single_value("Internal Ledger Settings", "company", old_company[0][0])
        else:
            frappe.db.sql("DELETE FROM `tabSingles` WHERE doctype=%s AND field=%s",
                          ("Internal Ledger Settings", "company"))
        frappe.db.commit()
        for path in paths:
            path.unlink(missing_ok=True)
        frappe.destroy()


if __name__ == "__main__":
    run(0)
    run(1)
