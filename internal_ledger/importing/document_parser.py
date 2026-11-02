from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import re

from internal_ledger.importing.models import NormalizedTransaction


_AMOUNT = re.compile(r"^-?\s*(?:CNY|[¥￥])?\s*([0-9]{1,3}(?:,[0-9]{3})*|[0-9]+)\.([0-9]{2})$")
_DATE_PATTERNS = ("%Y-%m-%d%H:%M:%S", "%Y-%m-%d%H:%M", "%Y年%m月%d日%H:%M:%S")


@dataclass(frozen=True)
class DocumentParseResult:
    source: str
    rows: list[NormalizedTransaction]
    errors: list[dict]


def _texts(page):
    return [str(line.get("text", "")).strip().rstrip(">") for line in page.get("lines", []) if line.get("text")]


def _value(lines, label):
    for index, text in enumerate(lines):
        if text.startswith(label):
            value = text[len(label):].lstrip("：: ")
            if value:
                return value
            if index + 1 < len(lines):
                return lines[index + 1]
    return None


def _amount(lines):
    candidates = []
    for text in lines:
        value = text.split("：", 1)[-1].strip().replace(" ", "")
        match = _AMOUNT.fullmatch(value)
        if match:
            candidates.append((Decimal(f"{match.group(1).replace(',', '')}.{match.group(2)}"), value.startswith("-")))
    if not candidates:
        raise ValueError("未识别到金额")
    return candidates[0]


def _datetime(value):
    compact = value.replace(" ", "")
    for pattern in _DATE_PATTERNS:
        try:
            return datetime.strptime(compact, pattern)
        except ValueError:
            continue
    raise ValueError("未识别到有效交易时间")


def _detect_source(pages):
    text = "\n".join(value for page in pages for value in _texts(page))
    if "业务回单" in text:
        return "bank"
    if "账单详情" in text and ("账单分类" in text or "商品说明" in text):
        return "alipay"
    if "交易详情" in text or "交易单号" in text:
        return "wechat"
    raise ValueError("无法识别文件来源，请手动选择银行、微信或支付宝")


def _bank(lines, payment_account, company, page_no):
    occurred_at = _datetime(_value(lines, "交易时间") or "")
    amount, _ = _amount(lines)
    payer = _value(lines, "付款户名") or ""
    payee = _value(lines, "收款户名") or ""
    payer_is_company, payee_is_company = company in payer, company in payee
    if payer_is_company == payee_is_company:
        raise ValueError("无法根据付款方和收款方判断收支方向")
    direction = "expense" if payer_is_company else "income"
    counterparty = payee if direction == "expense" else payer
    return NormalizedTransaction(
        source="bank", source_id=_value(lines, "流水号"), occurred_at=occurred_at, amount=amount,
        direction=direction, counterparty=counterparty, summary=_value(lines, "用途") or "银行转账",
        payment_account=payment_account, raw_row=page_no,
    )


def _mobile(source, lines, payment_account, page_no):
    amount, negative = _amount(lines)
    amount_index = next(index for index, text in enumerate(lines) if _AMOUNT.fullmatch(text.replace(" ", "")))
    if not negative:
        raise ValueError("截图金额未显示支出负号，请人工确认收支方向")
    if source == "alipay":
        counterparty = lines[amount_index - 1] if amount_index else ""
        summary = _value(lines, "商品说明")
        source_id = _value(lines, "交易号")
    else:
        counterparty = lines[amount_index + 1] if amount_index + 1 < len(lines) else ""
        summary = _value(lines, "商品")
        source_id = _value(lines, "交易单号")
    if not counterparty or not summary:
        raise ValueError("未识别到交易对象或商品摘要")
    return NormalizedTransaction(
        source=source, source_id=source_id, occurred_at=_datetime(_value(lines, "支付时间") or ""),
        amount=amount, direction="expense", counterparty=counterparty, summary=summary,
        payment_account=payment_account, raw_row=page_no,
    )


def parse_ocr_pages(source, pages, payment_account, company):
    if not pages:
        return DocumentParseResult(source if source != "auto" else "unknown", [], [{"raw_row": 1, "reason": "文件没有可识别页面"}])
    try:
        detected = _detect_source(pages)
    except ValueError as exc:
        if source == "auto":
            return DocumentParseResult("unknown", [], [{"raw_row": 1, "reason": str(exc)}])
        detected = source
    if source != "auto" and source != detected:
        return DocumentParseResult(source, [], [{"raw_row": 1, "reason": "文件内容与所选来源不一致"}])
    rows, errors = [], []
    for page_no, page in enumerate(pages, 1):
        try:
            lines = _texts(page)
            row = _bank(lines, payment_account, company, page_no) if detected == "bank" else _mobile(
                detected, lines, payment_account, page_no
            )
            rows.append(row)
        except (ValueError, InvalidOperation) as exc:
            errors.append({"raw_row": page_no, "reason": str(exc)})
    return DocumentParseResult(detected, rows, errors)
