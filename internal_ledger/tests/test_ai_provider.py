import json
import unittest

import frappe


FEATURE = "explain_report"
SECRET = "synthetic-test-secret"
SCHEMA = {
    "type": "object",
    "required": ["answer"],
    "properties": {"answer": {"type": "string"}},
    "additionalProperties": False,
}


class FakeTransport:
    def __init__(self, body=None, error=None):
        self.body = body
        self.error = error
        self.calls = []

    def __call__(self, request, timeout):
        self.calls.append((request, timeout))
        if self.error:
            raise self.error
        return 200, {"Content-Type": "application/json"}, self.body


def response(result):
    return json.dumps({
        "choices": [{"message": {"content": json.dumps(result)}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
    }).encode()


def configure(**overrides):
    values = {
        "enabled": 1,
        "base_url": "https://model.example.com/compatible",
        "model": "synthetic-model",
        "timeout_seconds": 15,
        "monthly_request_limit": 100,
        "allowed_features": FEATURE,
        "request_month": "",
        "request_count": 0,
    }
    values.update(overrides)
    settings = frappe.get_single("AI Provider Settings")
    settings.update(values)
    settings.api_key = SECRET
    settings.save(ignore_permissions=True)
    return settings


def provider(transport, feature=FEATURE):
    from internal_ledger.ai.provider import AIProvider

    return AIProvider(feature, transport=transport)


def assert_error(code, operation):
    from internal_ledger.ai.provider import AIProviderError

    with unittest.TestCase().assertRaises(AIProviderError) as caught:
        operation()
    assert caught.exception.code == code
    assert SECRET not in str(caught.exception)


def test_disabled_provider_stops_before_secret_or_transport_access():
    configure(enabled=0)
    transport = FakeTransport(response({"answer": "不应调用"}))
    assert_error("disabled", lambda: provider(transport).complete([], SCHEMA, 10))
    assert transport.calls == []


def test_non_https_and_unlisted_feature_are_rejected_before_transport():
    configure(base_url="http://model.example.com")
    transport = FakeTransport(response({"answer": "不应调用"}))
    assert_error("invalid_configuration", lambda: provider(transport).complete([], SCHEMA, 10))
    assert transport.calls == []

    configure(allowed_features="answer_help")
    assert_error("feature_not_allowed", lambda: provider(transport).complete([], SCHEMA, 10))
    assert transport.calls == []


def test_timeout_returns_safe_error_without_leaking_secret():
    configure()
    transport = FakeTransport(error=TimeoutError(f"timeout with {SECRET}"))
    assert_error("timeout", lambda: provider(transport).complete([], SCHEMA, 10))
    assert len(transport.calls) == 1


def test_non_json_response_is_rejected():
    configure()
    transport = FakeTransport(b"not-json")
    assert_error("invalid_response", lambda: provider(transport).complete([], SCHEMA, 10))


def test_schema_mismatch_is_rejected():
    configure()
    transport = FakeTransport(response({"answer": 7}))
    assert_error("schema_mismatch", lambda: provider(transport).complete([], SCHEMA, 10))


def test_monthly_limit_blocks_before_second_network_call():
    configure(monthly_request_limit=1)
    transport = FakeTransport(response({"answer": "一次成功"}))
    client = provider(transport)
    assert client.complete([], SCHEMA, 10)["answer"] == "一次成功"
    assert_error("rate_limit", lambda: client.complete([], SCHEMA, 10))
    assert len(transport.calls) == 1


def test_success_uses_fixed_https_path_bounded_timeout_and_json_schema():
    configure(timeout_seconds=8)
    transport = FakeTransport(response({"answer": "结构化结果"}))
    result = provider(transport).complete([{"role": "user", "content": "解释报表"}], SCHEMA, 30)
    request, timeout = transport.calls[0]
    payload = json.loads(request.data)

    assert result == {"answer": "结构化结果"}
    assert request.full_url == "https://model.example.com/compatible/v1/chat/completions"
    assert timeout == 8
    assert request.headers["Authorization"] == "Bearer " + SECRET
    assert payload["model"] == "synthetic-model"
    assert payload["response_format"]["json_schema"]["schema"] == SCHEMA


def tear_down():
    frappe.set_user("Administrator")
    frappe.db.sql("DELETE FROM `tabSingles` WHERE doctype=%s", "AI Provider Settings")
    frappe.db.commit()
    frappe.clear_cache(doctype="AI Provider Settings")


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(
        unittest.FunctionTestCase(test, tearDown=tear_down)
        for name, test in sorted(globals().items())
        if name.startswith("test_")
    )
