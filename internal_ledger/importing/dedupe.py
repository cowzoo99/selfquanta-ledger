import hashlib

from internal_ledger.importing.models import NormalizedTransaction


def build_dedupe_key(tx: NormalizedTransaction) -> str:
    if tx.source_id:
        return f"{tx.source}:{tx.source_id}"
    raw = "|".join((tx.payment_account, tx.occurred_at.isoformat(), str(tx.amount), tx.counterparty, tx.summary))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
