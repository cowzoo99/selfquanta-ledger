from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from threading import Barrier
import unittest
from uuid import uuid4

import frappe

from internal_ledger.importing.dedupe import build_dedupe_key
from internal_ledger.importing.models import NormalizedTransaction, save_batch
from internal_ledger.tests.test_install import make_company


def tx(**values):
    fields = dict(
        source="wechat", source_id="WX-1", occurred_at=datetime(2026, 10, 1, 10),
        amount=Decimal("12.50"), direction="expense", counterparty="测试商户",
        summary="办公用品", payment_account="微信余额", raw_row=2,
    )
    fields.update(values)
    if isinstance(fields["occurred_at"], str):
        fields["occurred_at"] = datetime.fromisoformat(fields["occurred_at"])
    return NormalizedTransaction(**fields)


def test_source_id_wins_and_fallback_is_stable():
    with_id = tx(source_id="WX-1", occurred_at="2026-10-01 10:00:00")
    assert build_dedupe_key(with_id) == "wechat:WX-1"
    no_id = tx(source_id=None, occurred_at="2026-10-01 10:00:00")
    assert build_dedupe_key(no_id) == build_dedupe_key(no_id)


def test_fallback_uses_transaction_fields_but_not_file_row():
    original = tx(source_id=None)
    key = build_dedupe_key(original)
    assert len(key) == 64
    assert build_dedupe_key(replace(original, raw_row=99)) == key
    for changes in (
        {"payment_account": "银行卡"}, {"occurred_at": datetime(2026, 10, 2, 10)},
        {"amount": Decimal("13.50")}, {"counterparty": "另一商户"}, {"summary": "差旅"},
    ):
        assert build_dedupe_key(replace(original, **changes)) != key
    assert build_dedupe_key(tx(source="alipay")) == "alipay:WX-1"
    assert build_dedupe_key(tx(summary="另一个摘要")) == "wechat:WX-1"


def test_save_batch_preserves_normalized_rows_and_settings():
    row = tx()
    name = save_batch("wechat", "file-hash", [row])
    batch = frappe.get_doc("Import Batch", name)
    assert batch.source == "wechat"
    assert batch.file_hash == "file-hash"
    assert batch.company == frappe.get_single("Internal Ledger Settings").company
    assert batch.base_currency == "CNY"
    saved = frappe.get_doc("Imported Transaction", {"import_batch": name})
    for field in ("source", "source_id", "direction", "counterparty", "summary", "payment_account", "raw_row"):
        assert saved.get(field) == getattr(row, field)
    assert saved.occurred_at == row.occurred_at
    assert Decimal(str(saved.amount)) == row.amount
    assert saved.status == "Imported"
    assert saved.dedupe_key == "wechat:WX-1"
    assert not saved.duplicate_of
    assert saved.docstatus == 0


def test_duplicates_are_retained_with_one_canonical_across_batches():
    first = save_batch("wechat", "same-file", [tx(), tx(raw_row=3), tx(raw_row=4)])
    second = save_batch("wechat", "same-file", [tx(raw_row=5)])
    rows = frappe.get_all("Imported Transaction", filters={"import_batch": ["in", [first, second]]},
                          fields=["name", "status", "dedupe_key", "duplicate_of", "raw_row"])
    canonical = [row for row in rows if row.status == "Imported"]
    duplicates = [row for row in rows if row.status == "Duplicate"]
    assert len(canonical) == 1
    assert len(duplicates) == 3
    assert {row.raw_row for row in rows} == {2, 3, 4, 5}
    assert all(not row.dedupe_key and row.duplicate_of == canonical[0].name for row in duplicates)
    assert frappe.db.count("Journal Entry") == frappe.flags.ledger_journal_count


def test_invalid_later_row_rolls_back_entire_batch_only():
    before = frappe.db.count("Import Batch")
    try:
        save_batch("wechat", "invalid-file", [tx(), tx(source_id="WX-2", direction="invalid")])
    except frappe.ValidationError:
        pass
    else:
        raise AssertionError("Invalid direction must reject the entire batch")
    assert frappe.db.count("Import Batch") == before
    assert not frappe.db.exists("Imported Transaction", {"dedupe_key": "wechat:WX-1"})
    assert frappe.get_single("Internal Ledger Settings").company


def test_database_unique_index_rejects_direct_duplicate_insert():
    batch = save_batch("wechat", "first", [tx()])
    original = frappe.get_doc("Imported Transaction", {"import_batch": batch})
    copied = frappe.copy_doc(original)
    try:
        copied.db_insert()
    except frappe.UniqueValidationError:
        pass
    else:
        raise AssertionError("Database must reject duplicate keys even without document validation")
    assert frappe.db.count("Imported Transaction", {"dedupe_key": "wechat:WX-1"}) == 1


def test_database_unique_index_arbitrates_concurrent_connections():
    site = frappe.local.site
    sites_path = frappe.local.sites_path
    barrier = Barrier(2)
    key = "wechat:concurrent-" + uuid4().hex

    def insert_one():
        frappe.init(site=site, sites_path=sites_path)
        frappe.connect()
        frappe.set_user("Administrator")
        try:
            doc = frappe.get_doc({
                "doctype": "Imported Transaction", "source": "wechat", "source_id": key,
                "occurred_at": datetime(2026, 10, 1, 10), "amount": 12.5, "direction": "expense",
                "counterparty": "测试商户", "summary": "办公用品", "payment_account": "微信余额",
                "raw_row": 2, "status": "Imported", "dedupe_key": key,
            })
            barrier.wait(timeout=20)
            try:
                doc.db_insert()
                frappe.db.commit()
                return "inserted"
            except frappe.UniqueValidationError:
                frappe.db.rollback()
                return "duplicate"
        finally:
            frappe.destroy()

    def verify_and_clean():
        frappe.init(site=site, sites_path=sites_path)
        frappe.connect()
        try:
            count = frappe.db.count("Imported Transaction", {"dedupe_key": key})
            frappe.db.delete("Imported Transaction", {"dedupe_key": key})
            frappe.db.commit()
            return count
        finally:
            frappe.destroy()

    with ThreadPoolExecutor(max_workers=2) as pool:
        try:
            results = list(pool.map(lambda _: insert_one(), range(2)))
        finally:
            count = pool.submit(verify_and_clean).result(timeout=30)
    assert sorted(results) == ["duplicate", "inserted"]
    assert count == 1


def test_batch_source_must_match_rows():
    before = frappe.db.count("Import Batch")
    try:
        save_batch("alipay", "mixed-source", [tx()])
    except frappe.ValidationError:
        pass
    else:
        raise AssertionError("A batch must not accept a row from a different source")
    assert frappe.db.count("Import Batch") == before


def load_tests(loader, tests, pattern):
    def set_up():
        frappe.db.rollback()
        settings = frappe.get_single("Internal Ledger Settings")
        settings.company = make_company()
        settings.base_currency = "CNY"
        settings.fiscal_year_start = "2026-01-01"
        settings.save()
        frappe.flags.ledger_journal_count = frappe.db.count("Journal Entry")

    def tear_down():
        # A full rollback also releases InnoDB locks before the concurrent test.
        frappe.db.rollback()
        frappe.clear_cache()

    return unittest.TestSuite(
        unittest.FunctionTestCase(test, setUp=set_up, tearDown=tear_down)
        for test in (
            test_source_id_wins_and_fallback_is_stable,
            test_fallback_uses_transaction_fields_but_not_file_row,
            test_save_batch_preserves_normalized_rows_and_settings,
            test_duplicates_are_retained_with_one_canonical_across_batches,
            test_invalid_later_row_rolls_back_entire_batch_only,
            test_database_unique_index_rejects_direct_duplicate_insert,
            test_database_unique_index_arbitrates_concurrent_connections,
            test_batch_source_must_match_rows,
        )
    )
