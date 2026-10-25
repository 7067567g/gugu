"""Real HTTP path for M3b image generation (local integration, NOT vendor smoke).

The vendor smoke required by the round goal needs real gateway credentials. This
environment has none (`gateway_credentials()` -> ``("","")``, no GUGU_IMAGE_* /
OPENAI_* env, no ~/.hermes/config.yaml), so the vendor call is recorded as
BLOCKED in ``runs/gugu-p0-m3b-global-reach/reports/G3-BLOCKED.md``.

What these tests DO prove: `images._real_generate` performs a genuine HTTP POST
to ``{base}/images/generations`` with the bearer key and model, handles both
``b64_json`` and ``url`` responses, and returns sniffed image bytes. The server
below is a local stub, clearly not a vendor, and no mock image is involved.
"""

from __future__ import annotations

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Iterator, List

import pytest

from plugins.gugu_capabilities import images

# 1x1 PNG (valid signature; the gateway path only sniffs the media type).
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII="
)


class _GatewayStub:
    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.calls: List[Dict[str, Any]] = []
        self.downloads = 0
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:  # keep pytest output clean
                return

            def _json(self, payload: Dict[str, Any], status: int = 200) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self) -> None:
                if self.path != "/images/generations":
                    self._json({"error": "not found"}, 404)
                    return
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length) or b"{}")
                stub.calls.append(
                    {
                        "authorization": self.headers.get("Authorization"),
                        "body": body,
                        "content_type": self.headers.get("Content-Type"),
                        "path": self.path,
                    }
                )
                if stub.mode == "url":
                    self._json({"data": [{"url": f"http://127.0.0.1:{stub.server.server_port}/download"}]})
                else:
                    self._json({"data": [{"b64_json": base64.b64encode(PNG_1X1).decode("ascii")}]})

            def do_GET(self) -> None:
                if self.path != "/download":
                    self._json({"error": "not found"}, 404)
                    return
                stub.downloads += 1
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(PNG_1X1)))
                self.end_headers()
                self.wfile.write(PNG_1X1)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    def __enter__(self) -> "_GatewayStub":
        self.thread.start()
        return self

    def __exit__(self, *args: Any) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


@pytest.fixture(params=["b64", "url"])
def gateway(request, monkeypatch) -> Iterator[_GatewayStub]:
    stub = _GatewayStub(request.param)
    with stub:
        monkeypatch.setenv("GUGU_IMAGE_BASE_URL", stub.base_url)
        monkeypatch.setenv("GUGU_IMAGE_API_KEY", "local-test-key-not-a-real-credential")
        for name in ("OPENAI_BASE_URL", "OPENAI_API_KEY", "GUGU_ALLOW_MOCK_IMAGES", "GUGU_IMAGE_MODE"):
            monkeypatch.delenv(name, raising=False)
        yield stub


def test_real_generate_posts_the_model_and_reads_the_image(gateway: _GatewayStub) -> None:
    data, media_type = images._real_generate(
        "an architectural diagram on warm paper",
        "qwen-image-2.0",
        "1024x1024",
        timeout=5.0,
    )

    assert media_type == "image/png"
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(gateway.calls) == 1
    call = gateway.calls[0]
    assert call["path"] == "/images/generations"
    assert call["authorization"] == "Bearer local-test-key-not-a-real-credential"
    assert call["body"]["model"] == "qwen-image-2.0"
    assert call["body"]["prompt"] == "an architectural diagram on warm paper"
    assert call["body"]["size"] == "1024x1024"
    if gateway.mode == "url":
        assert gateway.downloads == 1


def test_generate_with_fallback_uses_the_real_http_rung_not_mock(gateway: _GatewayStub) -> None:
    result = images.generate_with_fallback("warm editorial illustration", model="qwen-image-2.0")

    assert result["mock"] is False
    assert result["model"] == "qwen-image-2.0"
    assert result["media_type"] == "image/png"
    assert result["attempts"] == [{"model": "qwen-image-2.0", "ok": True}]
    assert gateway.calls[0]["body"]["model"] == "qwen-image-2.0"


def test_default_model_is_qwen_image_2_0(monkeypatch) -> None:
    monkeypatch.delenv("GUGU_IMAGE_MODEL_PRIMARY", raising=False)
    monkeypatch.delenv("GUGU_IMAGE_MODEL_FALLBACK", raising=False)
    monkeypatch.delenv("GUGU_IMAGE_MODELS", raising=False)

    assert images.model_candidates()[:2] == ["qwen-image-2.0", "gpt-image-2-medium"]
