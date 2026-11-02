import base64
import binascii
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json

import pymupdf
from rapidocr import RapidOCR


MAX_BYTES = 10 * 1024 * 1024
engine = RapidOCR()


def _ocr(image):
    result = engine(image)
    return [
        {"text": text, "score": round(float(score), 6)}
        for text, score in zip(result.txts or (), result.scores or ())
    ]


def extract(content):
    if content.startswith(b"%PDF-"):
        document = pymupdf.open(stream=content, filetype="pdf")
        if not document.page_count or document.page_count > 50:
            raise ValueError("PDF 页数必须为 1 至 50 页")
        pages = []
        for page in document:
            text = page.get_text().strip()
            lines = [{"text": line.strip(), "score": 1.0} for line in text.splitlines() if line.strip()]
            if len(text) < 20:
                image = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False).tobytes("png")
                lines = _ocr(image)
            pages.append({"lines": lines})
        return pages
    return [{"lines": _ocr(content)}]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        return

    def _reply(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._reply(200 if self.path == "/health" else 404, {"ok": self.path == "/health"})

    def do_POST(self):
        if self.path != "/extract":
            self._reply(404, {"error": "接口不存在"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 4 * MAX_BYTES // 3 + 4096:
                raise ValueError("文件大小必须为 1 字节至 10 MiB")
            request = json.loads(self.rfile.read(length))
            content = base64.b64decode(request.get("content_base64", ""), validate=True)
            if not content or len(content) > MAX_BYTES:
                raise ValueError("文件大小必须为 1 字节至 10 MiB")
            self._reply(200, {"pages": extract(content)})
        except (ValueError, KeyError, json.JSONDecodeError, binascii.Error) as exc:
            self._reply(400, {"error": str(exc) or "文件无法识别"})
        except Exception:
            self._reply(422, {"error": "OCR 无法识别该文件"})


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8765), Handler).serve_forever()
