import base64
import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def extract_pages(content, opener=urlopen):
    url = os.environ.get("OCR_SERVICE_URL", "http://ocr:8765").rstrip("/") + "/extract"
    body = json.dumps({"content_base64": base64.b64encode(content).decode()}).encode()
    request = Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with opener(request, timeout=120) as response:
            payload = json.load(response)
    except HTTPError as exc:
        try:
            reason = json.load(exc).get("error")
        except Exception:
            reason = None
        raise ValueError(reason or "OCR 服务拒绝了该文件") from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ValueError("OCR 服务暂时不可用，请稍后重试") from exc
    if not isinstance(payload.get("pages"), list):
        raise ValueError("OCR 服务返回格式无效")
    return payload["pages"]
