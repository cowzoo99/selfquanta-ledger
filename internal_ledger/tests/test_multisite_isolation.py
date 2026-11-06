"""Acceptance helpers for proving that two Frappe Sites cannot see each other.

Run ``prepare_fixture`` once on each disposable Site, then run this module on
both Sites with ``SELFLEDGER_TEST_SITE_A`` and ``SELFLEDGER_TEST_SITE_B`` set.
The evidence function intentionally returns only counts, booleans and hashes.
"""

from __future__ import annotations

import hashlib
import os
import unittest
from pathlib import Path

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
    companies = frappe.get_all("Company", pluck="name", order_by="creation asc", limit=2)
    if len(companies) != 1:
        raise AssertionError("Acceptance Site must contain exactly one Company")
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


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(
        unittest.FunctionTestCase(test)
        for test in (
            test_site_data_files_and_documents_are_isolated,
            test_site_config_and_storage_evidence_contains_hashes_only,
        )
    )
