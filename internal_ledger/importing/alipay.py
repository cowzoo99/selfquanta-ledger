from internal_ledger.importing.registry import _parse_csv


def parse_alipay(content: bytes, payment_account: str):
    return _parse_csv("alipay", content, payment_account, {
        "occurred_at": "交易创建时间", "amount": "金额（元）", "direction": "收/支",
        "counterparty": "交易对方", "summary": "商品名称", "source_id": "交易号",
    })
