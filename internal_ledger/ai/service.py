from internal_ledger.ai.provider import AIProvider


def get_provider(feature):
    return AIProvider(feature)
