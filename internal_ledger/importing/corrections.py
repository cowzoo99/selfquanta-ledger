from dataclasses import replace
from datetime import datetime
from decimal import Decimal, InvalidOperation


def apply_corrections(rows, values):
    if not isinstance(values, list) or len(values) != len(rows):
        raise ValueError("修正后的流水数量与预览不一致")
    corrected = []
    for original, value in zip(rows, values):
        if not isinstance(value, dict) or value.get("raw_row") != original.raw_row:
            raise ValueError("修正后的流水顺序与预览不一致")
        try:
            occurred_at = datetime.fromisoformat(str(value.get("occurred_at", "")))
            amount = Decimal(str(value.get("amount", "")))
        except (ValueError, InvalidOperation) as exc:
            raise ValueError("修正后的时间或金额格式无效") from exc
        if amount <= 0 or amount != amount.quantize(Decimal("0.01")):
            raise ValueError("修正后的金额必须大于零且最多两位小数")
        direction = value.get("direction")
        counterparty = str(value.get("counterparty", "")).strip()
        summary = str(value.get("summary", "")).strip()
        if direction not in {"income", "expense"} or not counterparty or not summary:
            raise ValueError("修正后的收支方向、交易对象和摘要不能为空")
        corrected.append(replace(
            original, occurred_at=occurred_at, amount=amount, direction=direction,
            counterparty=counterparty[:500], summary=summary[:500],
        ))
    return corrected
