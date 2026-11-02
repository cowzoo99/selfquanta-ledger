from datetime import datetime
from decimal import Decimal
import unittest


COMPANY = "逆熵云"
PAYMENT = "100201 - 基本存款账户"


def page(*texts):
    return {"lines": [{"text": text, "score": 0.99} for text in texts]}


class DocumentParserTest(unittest.TestCase):
    def test_preview_corrections_change_only_editable_fields(self):
        from internal_ledger.importing.corrections import apply_corrections
        from internal_ledger.importing.models import NormalizedTransaction

        original = NormalizedTransaction(
            source="wechat", source_id="fixed-id", occurred_at=datetime(2026, 10, 31, 19, 3, 39),
            amount=Decimal("78.00"), direction="expense", counterparty="原对象", summary="原摘要",
            payment_account=PAYMENT, raw_row=1,
        )
        changed = apply_corrections([original], [{
            "raw_row": 1, "occurred_at": "2026-10-31T19:04:00", "amount": "80.00",
            "direction": "expense", "counterparty": "新对象", "summary": "新摘要",
        }])[0]

        self.assertEqual((changed.amount, changed.occurred_at), (Decimal("80.00"), datetime(2026, 10, 31, 19, 4)))
        self.assertEqual((changed.counterparty, changed.summary), ("新对象", "新摘要"))
        self.assertEqual((changed.source, changed.source_id, changed.payment_account, changed.raw_row),
                         ("wechat", "fixed-id", PAYMENT, 1))

    def test_bank_receipt_uses_company_side_to_set_direction(self):
        from internal_ledger.importing.document_parser import parse_ocr_pages

        pages = [
            page("业务回单", "交易时间：2026-08-30 20:35", "流水号：12345678",
                 "付款户名：杭州逆熵云软件有限责任公司", "收款户名：测试供应商",
                 "金额：CNY2,000.00", "用途：报销采购货款"),
            page("业务回单", "交易时间：2026-08-25 18:10", "流水号：87654321",
                 "付款户名：测试客户", "收款户名：杭州逆熵云软件有限责任公司",
                 "金额：CNY50,000.00", "用途：投资款"),
        ]

        result = parse_ocr_pages("auto", pages, PAYMENT, COMPANY)

        self.assertEqual(result.source, "bank")
        self.assertEqual(result.errors, [])
        self.assertEqual([row.direction for row in result.rows], ["expense", "income"])
        self.assertEqual(result.rows[0].amount, Decimal("2000.00"))
        self.assertEqual(result.rows[0].occurred_at, datetime(2026, 8, 30, 20, 35))
        self.assertEqual(result.rows[0].counterparty, "测试供应商")
        self.assertEqual(result.rows[0].summary, "报销采购货款")
        self.assertEqual(result.rows[0].source_id, "12345678")
        self.assertEqual(result.rows[0].raw_row, 1)

    def test_alipay_screenshot_extracts_required_fields(self):
        from internal_ledger.importing.document_parser import parse_ocr_pages

        result = parse_ocr_pages("auto", [page(
            "账单详情", "高德打车", "-11.40", "自动扣款成功", "支付时间",
            "2026-10-31 12:17:28", "商品说明", "高德打车订单", "账单分类", "交通出行",
        )], PAYMENT, COMPANY)

        self.assertEqual(result.source, "alipay")
        self.assertEqual(result.errors, [])
        row = result.rows[0]
        self.assertEqual((row.amount, row.direction), (Decimal("11.40"), "expense"))
        self.assertEqual(row.occurred_at, datetime(2026, 10, 31, 12, 17, 28))
        self.assertEqual(row.counterparty, "高德打车")
        self.assertEqual(row.summary, "高德打车订单")

    def test_wechat_screenshot_extracts_required_fields(self):
        from internal_ledger.importing.document_parser import parse_ocr_pages

        result = parse_ocr_pages("auto", [page(
            "交易详情", "-78.00", "四个朋友", "当前状态", "支付成功", "支付时间",
            "2026年10月31日 19:03:39", "商品", "包间预定:测试门店", "商户全称", "测试公司",
            "交易单号", "4500000566202610310000000001",
        )], PAYMENT, COMPANY)

        self.assertEqual(result.source, "wechat")
        self.assertEqual(result.errors, [])
        row = result.rows[0]
        self.assertEqual((row.amount, row.direction), (Decimal("78.00"), "expense"))
        self.assertEqual(row.occurred_at, datetime(2026, 10, 31, 19, 3, 39))
        self.assertEqual(row.counterparty, "四个朋友")
        self.assertEqual(row.summary, "包间预定:测试门店")
        self.assertEqual(row.source_id, "4500000566202610310000000001")

    def test_missing_amount_is_an_error_not_a_guessed_transaction(self):
        from internal_ledger.importing.document_parser import parse_ocr_pages

        result = parse_ocr_pages("auto", [page("交易详情", "支付时间", "2026年10月31日 19:03:39")], PAYMENT, COMPANY)

        self.assertEqual(result.rows, [])
        self.assertEqual(result.errors[0]["raw_row"], 1)
        self.assertIn("金额", result.errors[0]["reason"])


if __name__ == "__main__":
    unittest.main()
