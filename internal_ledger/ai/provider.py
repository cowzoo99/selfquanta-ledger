import json
import re
import socket
import ssl
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import HTTPSHandler, HTTPRedirectHandler, Request, build_opener

import frappe
from frappe.utils import now_datetime


REQUEST_PATH = "/v1/chat/completions"
MAX_REQUEST_BYTES = 256 * 1024
MAX_RESPONSE_BYTES = 1024 * 1024


class AIProviderError(Exception):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _urllib_transport(request, timeout):
    opener = build_opener(HTTPSHandler(context=ssl.create_default_context()), _NoRedirect())
    with opener.open(request, timeout=timeout) as response:
        return response.status, dict(response.headers), response.read(MAX_RESPONSE_BYTES + 1)


def _feature_names(value):
    return {item for item in re.split(r"[\s,]+", value or "") if item}


def _matches_type(value, expected):
    types = {
        "object": dict,
        "array": list,
        "string": str,
        "number": (int, float),
        "integer": int,
        "boolean": bool,
        "null": type(None),
    }
    expected_type = types.get(expected)
    if expected_type is None:
        return False
    if expected in ("number", "integer") and isinstance(value, bool):
        return False
    return isinstance(value, expected_type)


def _validate_schema(value, schema, path="$/"):
    expected = schema.get("type")
    if expected and not _matches_type(value, expected):
        raise AIProviderError("schema_mismatch", f"AI 返回结构不符合要求：{path}")
    if "enum" in schema and value not in schema["enum"]:
        raise AIProviderError("schema_mismatch", f"AI 返回结构不符合要求：{path}")
    if expected == "object":
        properties = schema.get("properties", {})
        missing = set(schema.get("required", ())) - set(value)
        if missing:
            raise AIProviderError("schema_mismatch", f"AI 返回结构缺少字段：{path}")
        if schema.get("additionalProperties") is False and set(value) - set(properties):
            raise AIProviderError("schema_mismatch", f"AI 返回结构包含未知字段：{path}")
        for key, child_schema in properties.items():
            if key in value:
                _validate_schema(value[key], child_schema, f"{path}{key}/")
    if expected == "array" and "items" in schema:
        for index, item in enumerate(value):
            _validate_schema(item, schema["items"], f"{path}{index}/")


def _reserve_monthly_request(settings):
    period = now_datetime().strftime("%Y-%m")
    frappe.db.sql(
        "SELECT value FROM `tabSingles` WHERE doctype=%s AND field=%s FOR UPDATE",
        ("AI Provider Settings", "request_count"),
    )
    current_period = settings.request_month or ""
    current_count = int(settings.request_count or 0) if current_period == period else 0
    limit = int(settings.monthly_request_limit or 0)
    if limit <= 0 or current_count >= limit:
        raise AIProviderError("rate_limit", "本月 AI 使用额度已用完")
    frappe.db.set_single_value("AI Provider Settings", "request_month", period)
    frappe.db.set_single_value("AI Provider Settings", "request_count", current_count + 1)


class AIProvider:
    def __init__(self, feature, transport=None):
        self.feature = feature
        self._transport = transport or _urllib_transport

    def complete(self, messages, response_schema, timeout_seconds):
        settings = frappe.get_single("AI Provider Settings")
        if not settings.enabled:
            raise AIProviderError("disabled", "AI 功能当前未启用")
        endpoint = self._endpoint(settings.base_url)
        if not settings.model or not settings.timeout_seconds:
            raise AIProviderError("invalid_configuration", "AI 配置不完整")
        if self.feature not in _feature_names(settings.allowed_features):
            raise AIProviderError("feature_not_allowed", "当前 AI 功能未获启用")
        _reserve_monthly_request(settings)
        api_key = settings.get_password("api_key", raise_exception=False)
        if not api_key:
            raise AIProviderError("invalid_configuration", "AI 配置不完整")

        body = json.dumps({
            "model": settings.model,
            "messages": messages,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "selfledger_response",
                    "strict": True,
                    "schema": response_schema,
                },
            },
        }, ensure_ascii=False, separators=(",", ":")).encode()
        if len(body) > MAX_REQUEST_BYTES:
            raise AIProviderError("request_too_large", "AI 请求内容过大")
        request = Request(endpoint, data=body, method="POST", headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        configured_timeout = max(1, int(settings.timeout_seconds))
        timeout = min(max(1, int(timeout_seconds or configured_timeout)), configured_timeout)
        try:
            status, _headers, response_body = self._transport(request, timeout)
        except (TimeoutError, socket.timeout):
            raise AIProviderError("timeout", "AI 服务响应超时，请稍后重试") from None
        except (OSError, URLError):
            raise AIProviderError("provider_unavailable", "AI 服务暂时不可用") from None
        if not 200 <= status < 300 or len(response_body) > MAX_RESPONSE_BYTES:
            raise AIProviderError("invalid_response", "AI 服务返回异常")
        result = self._decode_response(response_body)
        _validate_schema(result, response_schema)
        return result

    @staticmethod
    def _endpoint(base_url):
        parts = urlsplit((base_url or "").strip())
        if (parts.scheme != "https" or not parts.netloc or parts.username or parts.password
                or parts.query or parts.fragment):
            raise AIProviderError("invalid_configuration", "AI 服务地址必须使用有效的 HTTPS 地址")
        return (base_url or "").rstrip("/") + REQUEST_PATH

    @staticmethod
    def _decode_response(body):
        try:
            envelope = json.loads(body.decode("utf-8"))
            content = envelope["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise TypeError
            result = json.loads(content)
            if not isinstance(result, dict):
                raise TypeError
            return result
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError):
            raise AIProviderError("invalid_response", "AI 服务未返回有效的结构化结果") from None
