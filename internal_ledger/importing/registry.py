import csv
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import io
import re

from frappe import ValidationError

from internal_ledger.importing.models import NormalizedTransaction


MAX_FILE_BYTES = 2 * 1024 * 1024
AMOUNT_PATTERN = re.compile(r"[+-]?(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]+)?")
PREAMBLES = {"wechat": "微信支付账单明细（脱敏测试）", "alipay": "支付宝交易记录（脱敏测试）"}


@dataclass(frozen=True)
class ParseResult:
    rows: list[NormalizedTransaction]
    errors: list[dict]
    file_hash: str


def _amount(value):
    value = value.removeprefix("¥").removeprefix("￥").strip()
    if not AMOUNT_PATTERN.fullmatch(value):
        raise ValueError("金额格式无效")
    try:
        amount = Decimal(value.replace(",", "")).copy_abs()
        cents = amount.quantize(Decimal("0.01"))
    except InvalidOperation as exc:
        raise ValueError("金额超出范围") from exc
    if not amount or amount != cents or amount >= Decimal("1000000000000"):
        raise ValueError("金额必须大于零、最多两位小数且小于一万亿元")
    return cents


def _datetime(value):
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M"):
        try:
            return datetime.strptime(value, pattern)
        except ValueError:
            continue
    raise ValueError("交易时间格式无效，需精确到分钟或秒")


def _parse_csv(source, content, payment_account, columns):
    if not isinstance(content, bytes) or not content or len(content) > MAX_FILE_BYTES:
        raise ValidationError("文件大小必须为 1 字节至 2 MiB")
    if not isinstance(payment_account, str) or not payment_account.strip():
        raise ValidationError("资金账户不能为空")
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            text = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValidationError("文件编码无效，仅支持 UTF-8 或 GB18030")
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
            if any(value and value != "-" and value[0] in "=+-@" for value in cells):
                raise ValidationError(f"第 {raw_row} 行单元格包含不允许的公式")
            if len(cells) == 1 and cells[0] == PREAMBLES.get(source):
                continue
            if len(cells) != len(set(cells)):
                raise ValidationError("账单表头包含重复字段")
            if not set(columns.values()).issubset(cells):
                raise ValidationError("未识别的账单表头，请使用支持的 CSV 表头")
            header = cells
            continue
        try:
            if len(cells) != len(header):
                raise ValueError("列数与表头不一致")
            fields = dict(zip(header, cells))
            for name, value in fields.items():
                if value and value != "-" and value[0] in "=+-@":
                    if name != columns["amount"] or not AMOUNT_PATTERN.fullmatch(value):
                        raise ValueError("单元格包含不允许的公式")
            values = {field: fields[name] for field, name in columns.items()}
            amount = _amount(values["amount"])
            occurred_at = _datetime(values["occurred_at"])
            if values["direction"] not in ("收入", "支出"):
                raise ValueError("收支方向无效，仅支持收入或支出")
            rows.append(NormalizedTransaction(
                source=source, source_id=values["source_id"] or None,
                occurred_at=occurred_at, amount=amount,
                direction={"收入": "income", "支出": "expense"}[values["direction"]],
                counterparty=values["counterparty"], summary=values["summary"],
                payment_account=payment_account.strip(), raw_row=raw_row,
            ))
        except ValueError as exc:
            errors.append({"raw_row": raw_row, "reason": str(exc)})
    if header is None:
        raise ValidationError("未识别的账单表头，请使用支持的 CSV 表头")
    return ParseResult(rows, errors, hashlib.sha256(content).hexdigest())


def parse_statement(source: str, content: bytes, payment_account: str) -> ParseResult:
    from internal_ledger.importing.bank import parse_bank
    from internal_ledger.importing.wechat import parse_wechat
    from internal_ledger.importing.alipay import parse_alipay

    parsers = {"bank": parse_bank, "wechat": parse_wechat, "alipay": parse_alipay}
    if not isinstance(source, str) or source not in parsers:
        raise ValidationError(f"不支持的账单来源: {source}")
    return parsers[source](content, payment_account)
