import csv
from datetime import datetime
from decimal import Decimal
import hashlib
import io

from frappe import ValidationError

from internal_ledger.importing.models import NormalizedTransaction
from internal_ledger.importing.registry import (
    AMOUNT_PATTERN, MAX_CSV_BYTES, ParseResult, _amount, _parse_csv,
)


CHOUZHOU_TITLE = "浙江稠州商业银行账户交易明细"
CHOUZHOU_HEADER = (
    "序号", "交易日期", "交易金额(收入)", "交易金额(支出)",
    "账户余额", "对方账号", "对方户名", "用途",
)


def _decode_chouzhou(content):
    if not isinstance(content, bytes) or not content or len(content) > MAX_CSV_BYTES:
        return None
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            text = content.decode(encoding)
            return text if CHOUZHOU_TITLE in text.splitlines()[0] else None
        except UnicodeDecodeError:
            continue
    return None


def _parse_chouzhou(content, text, payment_account):
    if not isinstance(payment_account, str) or not payment_account.strip():
        raise ValidationError("资金账户不能为空")
    if "\x00" in text:
        raise ValidationError("文件编码无效，包含空字符")
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    rows, errors, header = [], [], None
    while True:
        raw_row = reader.line_num + 1
        try:
            cells = next(reader)
        except StopIteration:
            break
        except csv.Error:
            errors.append({"raw_row": raw_row, "reason": "CSV 格式无效或单元格过长"})
            break
        cells = [cell.strip() for cell in cells]
        if not any(cells):
            continue
        if header is None:
            if tuple(cells) == CHOUZHOU_HEADER:
                header = cells
                continue
            if any(value and value != "-" and value[0] in "=+-@" for value in cells):
                raise ValidationError(f"第 {raw_row} 行单元格包含不允许的公式")
            continue
        try:
            if cells[0] == "合计":
                break
            if len(cells) != len(header):
                raise ValueError("列数与表头不一致")
            fields = dict(zip(header, cells))
            monetary_fields = {"交易金额(收入)", "交易金额(支出)", "账户余额"}
            for name, value in fields.items():
                if value and value != "-" and value[0] in "=+-@":
                    if name not in monetary_fields or not AMOUNT_PATTERN.fullmatch(value):
                        raise ValueError("单元格包含不允许的公式")
            income, expense = fields["交易金额(收入)"], fields["交易金额(支出)"]
            if income != "-" and expense == "-":
                direction, raw_amount = "income", income
            elif expense != "-" and income == "-":
                direction, raw_amount = "expense", expense
            else:
                raise ValueError("收入和支出必须且只能填写一项")
            if (AMOUNT_PATTERN.fullmatch(raw_amount)
                    and Decimal(raw_amount.replace(",", "")) == 0):
                continue
            rows.append(NormalizedTransaction(
                source="bank", source_id=None,
                occurred_at=datetime.strptime(fields["交易日期"], "%Y-%m-%d"),
                amount=_amount(raw_amount), direction=direction,
                counterparty=fields["对方户名"], summary=fields["用途"],
                payment_account=payment_account.strip(), raw_row=raw_row,
            ))
        except ValueError as exc:
            errors.append({"raw_row": raw_row, "reason": str(exc)})
    if header is None:
        raise ValidationError("未识别的账单表头，请使用支持的 CSV 表头")
    return ParseResult(rows, errors, hashlib.sha256(content).hexdigest(), "bank")


def parse_bank(content: bytes, payment_account: str):
    chouzhou_text = _decode_chouzhou(content)
    if chouzhou_text is not None:
        return _parse_chouzhou(content, chouzhou_text, payment_account)
    return _parse_csv("bank", content, payment_account, {
        "occurred_at": "交易时间", "amount": "交易金额", "direction": "收支",
        "counterparty": "对方户名", "summary": "摘要", "source_id": "流水号",
    })
