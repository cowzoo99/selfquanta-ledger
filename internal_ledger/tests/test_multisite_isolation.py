"""Acceptance helpers for proving that two Frappe Sites cannot see each other.

Run ``prepare_fixture`` once on each disposable Site, then run this module on
both Sites with ``SELFLEDGER_TEST_SITE_A`` and ``SELFLEDGER_TEST_SITE_B`` set.
The evidence function intentionally returns only counts, booleans and hashes.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import unittest
from pathlib import Path
from zipfile import ZipFile

import frappe
from frappe.utils import getdate, nowdate
from internal_ledger.permissions import _JOURNAL_WRITE


SITE_A_ENV = "SELFLEDGER_TEST_SITE_A"
SITE_B_ENV = "SELFLEDGER_TEST_SITE_B"


def _sites() -> tuple[str, str]:
    sites = tuple((os.environ.get(name) or "").strip() for name in (SITE_A_ENV, SITE_B_ENV))
    if not all(sites) or sites[0] == sites[1]:
        raise AssertionError("Two distinct acceptance Site names are required")
    return sites


def _site() -> str:
    site = str(frappe.local.site)
    if site not in _sites():
        raise AssertionError("Run the isolation test only on an approved acceptance Site")
    return site


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _marker(site: str) -> str:
    return "g1-" + _digest(site)[:20]


def _peer(site: str) -> str:
    first, second = _sites()
    return second if site == first else first


def _company() -> str:
    fixture_company = frappe.db.get_value("Journal Entry", {"user_remark": _marker(_site())}, "company")
    if fixture_company:
        return fixture_company
    companies = [name for name in frappe.get_all("Company", pluck="name", order_by="creation asc")
                 if not name.startswith("_Test")]
    if len(companies) != 1:
        raise AssertionError("Acceptance Site must contain exactly one non-test Company")
    return companies[0]


def _accounts(company: str) -> tuple[str, str]:
    rows = frappe.get_all(
        "Account",
        filters={"company": company, "is_group": 0, "account_currency": "CNY"},
        fields=["name", "root_type"],
        order_by="name asc",
    )
    debit = next((row.name for row in rows if row.root_type == "Asset"), None)
    credit = next((row.name for row in rows if row.root_type in {"Equity", "Liability", "Income"}), None)
    if not debit or not credit or debit == credit:
        raise AssertionError("Acceptance Site requires two CNY ledger accounts")
    return debit, credit


def prepare_fixture() -> dict:
    """Create one idempotent, synthetic Customer/File/Journal Entry fixture."""
    site = _site()
    marker = _marker(site)
    company = _company()
    customer_name = f"G1-{marker}"
    customer = frappe.db.get_value("Customer", {"customer_name": customer_name}, "name")
    if not customer:
        customer = frappe.get_doc(
            {"doctype": "Customer", "customer_name": customer_name, "customer_type": "Company"}
        ).insert(ignore_permissions=True).name

    file_name = f"{marker}.txt"
    if not frappe.db.exists("File", {"attached_to_doctype": "Customer", "attached_to_name": customer}):
        frappe.get_doc(
            {
                "doctype": "File",
                "file_name": file_name,
                "content": marker,
                "is_private": 1,
                "attached_to_doctype": "Customer",
                "attached_to_name": customer,
            }
        ).insert(ignore_permissions=True)

    if not frappe.db.exists("Journal Entry", {"user_remark": marker}):
        debit, credit = _accounts(company)
        journal = frappe.get_doc(
            {
                "doctype": "Journal Entry",
                "voucher_type": "Journal Entry",
                "company": company,
                "posting_date": getdate(nowdate()),
                "user_remark": marker,
                "accounts": [
                    {"account": debit, "debit_in_account_currency": 1},
                    {"account": credit, "credit_in_account_currency": 1},
                ],
            }
        )
        journal.flags.ledger_journal_write = _JOURNAL_WRITE
        journal.insert(ignore_permissions=True)
    frappe.db.commit()
    return isolation_evidence()


def _fixture_counts(marker: str) -> dict[str, int]:
    customer = frappe.db.get_value("Customer", {"customer_name": f"G1-{marker}"}, "name")
    return {
        "Customer": frappe.db.count("Customer", {"customer_name": f"G1-{marker}"}),
        "File": frappe.db.count("File", {"attached_to_doctype": "Customer", "attached_to_name": customer})
        if customer else 0,
        "Journal Entry": frappe.db.count("Journal Entry", {"user_remark": marker}),
    }


def isolation_evidence() -> dict:
    """Return non-secret evidence for the active Site and its peer."""
    site = _site()
    own_counts = _fixture_counts(_marker(site))
    peer_counts = _fixture_counts(_marker(_peer(site)))
    site_path = Path(frappe.get_site_path()).resolve()
    return {
        "site_hash": _digest(site),
        "database_hash": _digest(str(frappe.conf.db_name)),
        "encryption_key_hash": _digest(str(frappe.conf.encryption_key)),
        "admin_identity_hash": _digest(f"{site}\0Administrator"),
        "private_files_hash": _digest(str(site_path / "private" / "files")),
        "backup_directory_hash": _digest(str(site_path / "private" / "backups")),
        "own_fixture_counts": own_counts,
        "peer_fixture_counts": peer_counts,
        "own_fixture_complete": all(value == 1 for value in own_counts.values()),
        "peer_fixture_visible": any(peer_counts.values()),
    }


def _enterprise_owner() -> str:
    email = f"g1-owner-{_digest(_site())[:12]}@example.invalid"
    if not frappe.db.exists("User", email):
        frappe.get_doc({
            "doctype": "User",
            "email": email,
            "first_name": "G1 acceptance owner",
            "send_welcome_email": 0,
            "roles": [{"role": "企业负责人"}],
        }).insert(ignore_permissions=True)
    elif not frappe.db.exists("Has Role", {
        "parent": email, "parenttype": "User", "role": "企业负责人",
    }):
        frappe.get_doc({
            "doctype": "Has Role",
            "parent": email,
            "parenttype": "User",
            "parentfield": "roles",
            "role": "企业负责人",
        }).insert(ignore_permissions=True)
    return email


def customer_exit_evidence() -> dict:
    """Open a real package and return only non-secret isolation evidence."""
    from internal_ledger.api.export_api import request_customer_exit_export

    previous_user = frappe.session.user
    previous_in_test = frappe.flags.in_test
    try:
        frappe.flags.in_test = True
        frappe.db.set_single_value("Internal Ledger Settings", "company", _company())
        frappe.set_user(_enterprise_owner())
        result = request_customer_exit_export()
        if result["status"] != "Ready":
            raise AssertionError(
                f"Customer exit package is {result['status']}: {result['failure_code']}"
            )
        file_name = frappe.db.get_value("File", {
            "attached_to_doctype": "Customer Exit Export",
            "attached_to_name": result["name"],
            "file_url": result["file"],
        }, "name")
        package = Path(frappe.get_doc("File", file_name).get_full_path())
        with ZipFile(package) as archive:
            names = archive.namelist()
            manifest = json.loads(archive.read("manifest.json"))
            required = {
                "master-data/customer.csv", "transactions/journal-entry.csv",
                "attachments/index.csv", "audit/internal-ledger-audit-event.csv",
            }
            if not required.issubset(names):
                raise AssertionError("Customer exit package is missing a required readable member")
            for member in required:
                archive.read(member)
            attachment_member = next((member for member in names if member.startswith("attachments/files/")), None)
            if not attachment_member:
                raise AssertionError("Customer exit package has no readable attachment")
            archive.read(attachment_member)
            attachment_rows = list(csv.reader(io.StringIO(
                archive.read("attachments/index.csv").decode("utf-8-sig")
            )))[1:]
            if (manifest["row_count"] != int(result["row_count"])
                    or manifest["attachment_count"] != int(result["attachment_count"])
                    or len(attachment_rows) != int(result["attachment_count"])):
                raise AssertionError("Customer exit package counts do not match the stored request")
            searchable = b"\n".join(
                archive.read(member)
                for member in names
                if member.endswith((".csv", ".txt")) or member.startswith("attachments/files/")
            )
        own_marker = _marker(_site()).encode("utf-8")
        peer_marker = _marker(_peer(_site())).encode("utf-8")
        return {
            "package_sha256": result["package_sha256"],
            "row_count": int(result["row_count"]),
            "attachment_count": int(result["attachment_count"]),
            "audit_count": int(manifest["audit_count"]),
            "own_marker_present": own_marker in searchable,
            "peer_marker_present": peer_marker in searchable,
        }
    finally:
        frappe.flags.in_test = previous_in_test
        frappe.set_user(previous_user)


def test_site_data_files_and_documents_are_isolated():
    evidence = isolation_evidence()
    assert evidence["own_fixture_complete"]
    assert not evidence["peer_fixture_visible"]
    assert evidence["peer_fixture_counts"] == {"Customer": 0, "File": 0, "Journal Entry": 0}


def test_site_config_and_storage_evidence_contains_hashes_only():
    evidence = isolation_evidence()
    for key in (
        "site_hash",
        "database_hash",
        "encryption_key_hash",
        "admin_identity_hash",
        "private_files_hash",
        "backup_directory_hash",
    ):
        assert len(evidence[key]) == 64 and set(evidence[key]) <= set("0123456789abcdef")
    serialized = repr(evidence)
    assert str(frappe.conf.db_name) not in serialized
    assert str(frappe.conf.encryption_key) not in serialized


def test_customer_exit_package_contains_own_fixture_and_not_peer_fixture():
    evidence = customer_exit_evidence()
    assert evidence["package_sha256"]
    assert evidence["own_marker_present"]
    assert not evidence["peer_marker_present"]
    assert evidence["attachment_count"] >= 1
    assert evidence["audit_count"] >= 1


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(
        unittest.FunctionTestCase(test)
        for test in (
            test_site_data_files_and_documents_are_isolated,
            test_site_config_and_storage_evidence_contains_hashes_only,
            test_customer_exit_package_contains_own_fixture_and_not_peer_fixture,
        )
    )
