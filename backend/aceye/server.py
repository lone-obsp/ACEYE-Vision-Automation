from __future__ import annotations

import json
import mimetypes
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .service import AceyeService


class SingleInstanceHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer with port reuse disabled.

    http.server defaults to SO_REUSEADDR, which on Windows allows a second
    ACEYE process to bind the very same 127.0.0.1:8765 while the first one
    keeps listening - the frontend then load-balances between two backends
    with different configs and the UI state (mode/profile/rhythm) flaps.
    Disabling reuse makes the second bind fail with EADDRINUSE instead.
    """

    allow_reuse_address = False
    daemon_threads = True


class AceyeServer:
    def __init__(self, root: Path, host: str = "127.0.0.1", port: int = 8765) -> None:
        self.root = root.resolve()
        self.frontend = self.root / "frontend"
        self.service = AceyeService(self.root)
        self.host, self.port = host, port
        service = self.service
        frontend = self.frontend

        class Handler(BaseHTTPRequestHandler):
            server_version = "ACEYE/0.2"

            def log_message(self, format: str, *args: Any) -> None:
                service.state.log("debug", format % args)

            def _authorized(self, *, required: bool = True) -> bool:
                if not required:
                    return True
                return self.headers.get("X-ACEYE-Token", "") == service.token

            def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _read_json(self) -> dict[str, Any]:
                size = min(int(self.headers.get("Content-Length", "0")), 1024 * 1024)
                raw = self.rfile.read(size) if size else b"{}"
                value = json.loads(raw.decode("utf-8"))
                if not isinstance(value, dict):
                    raise ValueError("JSON body must be an object")
                return value

            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path == "/api/health":
                    self._send_json(service.health())
                    return
                if parsed.path.startswith("/api/") and not self._authorized():
                    self._send_json({"ok": False, "message": "未授权"}, HTTPStatus.UNAUTHORIZED)
                    return
                if parsed.path == "/api/state":
                    self._send_json(service.snapshot())
                    return
                if parsed.path == "/api/models":
                    self._send_json(service.model_status())
                    return
                if parsed.path == "/api/rhythm/status":
                    self._send_json(service.rhythm_status())
                    return
                self._serve_static(parsed.path)

            def _serve_static(self, path: str) -> None:
                relative = path.removeprefix("/") or "index.html"
                candidate = (frontend / relative).resolve()
                if frontend not in candidate.parents and candidate != frontend:
                    self._send_json({"ok": False, "message": "非法路径"}, HTTPStatus.BAD_REQUEST)
                    return
                if not candidate.exists() or not candidate.is_file():
                    candidate = frontend / "index.html"
                body = candidate.read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", mimetypes.guess_type(str(candidate))[0] or "application/octet-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                if not self._authorized():
                    self._send_json({"ok": False, "message": "未授权"}, HTTPStatus.UNAUTHORIZED)
                    return
                try:
                    body = self._read_json()
                    if parsed.path == "/api/start": result = service.start()
                    elif parsed.path == "/api/stop": result = service.stop()
                    elif parsed.path == "/api/emergency": result = service.emergency()
                    elif parsed.path == "/api/shutdown": result = service.stop()
                    elif parsed.path == "/api/config": result = service.update_config(body)
                    elif parsed.path == "/api/command": result = service.command(str(body.get("text", "")))
                    elif parsed.path == "/api/prepare": result = service.prepare(str(body.get("profile", "")))
                    elif parsed.path == "/api/reason": result = service.reason()
                    elif parsed.path == "/api/segment": result = service.segment()
                    elif parsed.path == "/api/rhythm/start": result = service.start_rhythm(
                        str(body.get("game", "")) or None,
                    )
                    elif parsed.path == "/api/rhythm/stop": result = service.stop_rhythm()
                    else:
                        self._send_json({"ok": False, "message": "接口不存在"}, HTTPStatus.NOT_FOUND)
                        return
                    self._send_json(result, 200 if result.get("ok", False) else 400)
                except Exception as exc:  # noqa: BLE001
                    self._send_json({"ok": False, "message": str(exc)}, HTTPStatus.BAD_REQUEST)

        try:
            self.httpd = SingleInstanceHTTPServer((self.host, self.port), Handler)
        except OSError as exc:
            raise RuntimeError(
                f"端口 {self.host}:{self.port} 已被占用（{exc}）。"
                "可能已有一个 ACEYE 实例在运行；请先停止旧实例再启动。"
            ) from exc

    def serve_forever(self) -> None:
        self.httpd.serve_forever(poll_interval=0.25)

    def shutdown(self) -> None:
        self.service.stop()
        self.httpd.shutdown()
        self.httpd.server_close()
