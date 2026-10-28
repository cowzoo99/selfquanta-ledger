import base64
import hashlib
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
import unittest

import frappe

from internal_ledger.tests.test_install import make_company


def fixture(source):
    return (Path(__file__).parent / "fixtures" / f"{source}.csv").read_bytes()


def parse(source, content=None):
    from internal_ledger.importing.registry import parse_statement
    return parse_statement(source, fixture(source) if content is None else content, "测试资金账户")


def rejects(call, message):
    try:
        call()
    except frappe.ValidationError as exc:
        assert message in str(exc), str(exc)
    else:
        raise AssertionError("Expected explicit rejection: " + message)


def test_bank_contract():
    assert_contract("bank", "BANK-001", 2)


def test_wechat_contract():
    assert_contract("wechat", "WX-001", 3)


def test_alipay_contract():
    assert_contract("alipay", "ALI-001", 3)


def assert_contract(source, source_id, raw_row):
    result = parse(source)
    assert len(result.rows) == 3
    assert not result.errors
    assert all(row.amount > 0 for row in result.rows)
    assert {row.direction for row in result.rows} == {"income", "expense"}
    row = result.rows[0]
    assert row.source == source
    assert row.source_id == source_id
    assert row.occurred_at == datetime(2026, 10, 1, 10)
    assert row.amount == Decimal("12.50")
    assert row.payment_account == "测试资金账户"
    assert row.raw_row == raw_row
    assert row.counterparty == "测试商户甲"
    assert row.summary == "办公用品"
    assert result.file_hash == hashlib.sha256(fixture(source)).hexdigest()


def test_normalization_gives_identical_fallback_keys_across_sources():
    from dataclasses import replace
    from internal_ledger.importing.dedupe import build_dedupe_key
    rows = [replace(parse(source).rows[0], source_id=None) for source in ("bank", "wechat", "alipay")]
    assert len({str(row.amount) for row in rows}) == 1
    assert len({build_dedupe_key(row) for row in rows}) == 1
    content = fixture("bank").replace(b"10:00:00", b"10:00").replace(b"-12.50", b"12.5000")
    assert build_dedupe_key(replace(parse("bank", content).rows[0], source_id=None)) == build_dedupe_key(rows[0])


def test_headers_are_mapped_by_name_not_position():
    content = "流水号,摘要,收支,交易金额,交易时间,对方户名\nB-1,办公用品,支出,12.50,2026/10/01 10:00,测试商户甲\n".encode()
    row = parse("bank", content).rows[0]
    assert row.amount == Decimal("12.50")
    assert row.summary == "办公用品"
    assert row.occurred_at == datetime(2026, 10, 1, 10)


def test_utf8_bom_and_gb18030_are_supported():
    text = fixture("bank").decode()
    for content in (text.encode("utf-8-sig"), text.encode("gb18030")):
        assert len(parse("bank", content).rows) == 3


def test_unknown_source_headers_encoding_and_size_are_rejected():
    rejects(lambda: parse("unknown", b"a,b\n1,2"), "不支持")
    rejects(lambda: parse("bank", "时间,金额\n2026-01-01,1".encode()), "表头")
    rejects(lambda: parse("bank", b"\xff\xff\xff"), "编码")
    rejects(lambda: parse("bank", b"x" * (2 * 1024 * 1024 + 1)), "大小")


def test_errors_keep_physical_line_numbers_and_chinese_reasons():
    header = fixture("bank").splitlines()[0].decode()
    content = (header + '\n2026-10-01 10:00:00,12,支出,测试商户,"跨行\n摘要",B-1\n'
               '2026-10-02 10:00:00,NaN,支出,测试商户,坏金额,B-2\n'
               '坏日期,12,支出,测试商户,坏日期,B-3\n'
               '2026-10-02 10:00:00,12,不计收支,测试商户,坏方向,B-4\n').encode()
    result = parse("bank", content)
    assert len(result.rows) == 1
    assert [error["raw_row"] for error in result.errors] == [4, 5, 6]
    for error, reason in zip(result.errors, ("金额", "时间", "收支")):
        assert reason in error["reason"]


def test_formula_cells_are_errors_for_every_adapter():
    for source in ("bank", "wechat", "alipay"):
        for formula in ('=1+1', '+SUM(1)', '-SUM(1)', '@SUM(1)'):
            result = parse(source, fixture(source).replace("办公用品".encode(), formula.encode()))
            assert len(result.rows) == 2
            assert len(result.errors) == 1
            assert "公式" in result.errors[0]["reason"]


def test_amounts_are_never_silently_rounded():
    for amount in (b"12.50000000000000000000000000001", b"12.501", b"0", b"Infinity", b"1000000000000"):
        result = parse("bank", fixture("bank").replace(b"-12.50", amount))
        assert len(result.rows) == 2, amount
        assert len(result.errors) == 1
        assert "金额" in result.errors[0]["reason"]


def test_preview_is_serializable_and_does_not_write():
    from internal_ledger.api.import_api import preview_import
    before = [frappe.db.count(doctype) for doctype in ("Import Batch", "Imported Transaction", "Journal Entry")]
    result = preview_import("bank", base64.b64encode(fixture("bank")).decode(), "测试资金账户")
    assert len(result["rows"]) == 3
    assert result["rows"][0]["amount"] == "12.50"
    json.dumps(result, ensure_ascii=False)
    assert [frappe.db.count(doctype) for doctype in ("Import Batch", "Imported Transaction", "Journal Entry")] == before


def test_confirm_reparses_content_and_recomputes_hash():
    from internal_ledger.api.import_api import preview_import, confirm_import
    encoded = base64.b64encode(fixture("bank")).decode()
    preview = preview_import("bank", encoded, "测试资金账户")
    altered = base64.b64encode(fixture("bank").replace(b"12.50", b"99.50")).decode()
    before = frappe.db.count("Import Batch")
    rejects(lambda: confirm_import("bank", altered, "测试资金账户", preview["file_hash"]), "哈希")
    assert frappe.db.count("Import Batch") == before
    preview["rows"][0]["amount"] = "99999.00"
    result = confirm_import("bank", encoded, "测试资金账户", preview["file_hash"])
    batch = frappe.get_doc("Import Batch", result["batch_name"])
    assert batch.file_hash == hashlib.sha256(fixture("bank")).hexdigest()
    rows = frappe.get_all("Imported Transaction", filters={"import_batch": batch.name}, fields=["amount", "source_id"])
    assert len(rows) == 3
    assert Decimal(str(next(row.amount for row in rows if row.source_id == "BANK-001"))) == Decimal("12.50")


def test_confirm_blocks_errors_and_api_rejects_invalid_base64():
    from internal_ledger.api.import_api import preview_import, confirm_import
    content = fixture("bank").replace(b"12.50", b"NaN")
    encoded = base64.b64encode(content).decode()
    result = preview_import("bank", encoded, "测试资金账户")
    before = frappe.db.count("Import Batch")
    rejects(lambda: confirm_import("bank", encoded, "测试资金账户", result["file_hash"]), "错误")
    rejects(lambda: preview_import("bank", "***", "测试资金账户"), "Base64")
    assert frappe.db.count("Import Batch") == before


def test_api_keeps_frappe_create_permissions():
    from internal_ledger.api.import_api import preview_import, confirm_import
    encoded = base64.b64encode(fixture("bank")).decode()
    file_hash = hashlib.sha256(fixture("bank")).hexdigest()
    frappe.set_user("Guest")
    try:
        for call in (lambda: preview_import("bank", encoded, "测试资金账户"),
                     lambda: confirm_import("bank", encoded, "测试资金账户", file_hash)):
            try:
                call()
            except frappe.PermissionError:
                pass
            else:
                raise AssertionError("Guest must not be allowed to import")
    finally:
        frappe.set_user("Administrator")


def load_tests(loader, tests, pattern):
    def set_up():
        frappe.db.rollback()
        frappe.set_user("Administrator")
        settings = frappe.get_single("Internal Ledger Settings")
        settings.company = make_company()
        settings.base_currency = "CNY"
        settings.fiscal_year_start = "2026-01-01"
        settings.save()

    def tear_down():
        frappe.set_user("Administrator")
        frappe.db.rollback()
        frappe.clear_cache()

    return unittest.TestSuite(
        unittest.FunctionTestCase(test, setUp=set_up, tearDown=tear_down)
        for name, test in sorted(globals().items()) if name.startswith("test_")
    )
