from internal_ledger.importing.registry import _parse_csv


def parse_bank(content: bytes, payment_account: str):
    return _parse_csv("bank", content, payment_account, {
        "occurred_at": "交易时间", "amount": "交易金额", "direction": "收支",
        "counterparty": "对方户名", "summary": "摘要", "source_id": "流水号",
    })
