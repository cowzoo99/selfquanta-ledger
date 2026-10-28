from internal_ledger.importing.registry import _parse_csv


def parse_wechat(content: bytes, payment_account: str):
    return _parse_csv("wechat", content, payment_account, {
        "occurred_at": "交易时间", "amount": "金额(元)", "direction": "收/支",
        "counterparty": "交易对方", "summary": "商品", "source_id": "交易单号",
    })
