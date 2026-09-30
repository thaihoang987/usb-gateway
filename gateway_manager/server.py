from __future__ import annotations

import json
import mimetypes
import os
import signal
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__
from .config import ConfigError, ConfigStore
from .manager import GatewayManager


MAX_BODY = 256 * 1024
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


class ApiHandler(BaseHTTPRequestHandler):
    manager: GatewayManager
    server_version = f"USBGateway/{__version__}"

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[http] {self.address_string()} {fmt % args}", flush=True)

    def _json(self, status: int, payload: object) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ConfigError("invalid Content-Length") from exc
        if length <= 0 or length > MAX_BODY:
            raise ConfigError("request body is empty or too large")
        try:
            payload = json.loads(self.rfile.read(length))
        except json.JSONDecodeError as exc:
            raise ConfigError("invalid JSON") from exc
        if not isinstance(payload, dict):
            raise ConfigError("JSON body must be an object")
        return payload

    def _parts(self) -> list[str]:
        return [part for part in urlparse(self.path).path.split("/") if part]

    def _serve_static(self) -> None:
        path = urlparse(self.path).path
        relative = "index.html" if path in {"", "/"} else path.lstrip("/")
        target = (STATIC_DIR / relative).resolve()
        if STATIC_DIR.resolve() not in target.parents and target != STATIC_DIR.resolve():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = target.read_bytes()
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        try:
            parts = self._parts()
            query = parse_qs(urlparse(self.path).query)
            if parts == ["api", "health"]:
                self._json(HTTPStatus.OK, {"ok": True, "version": __version__})
            elif parts == ["api", "ports"]:
                self._json(HTTPStatus.OK, {"ports": self.manager.list_ports()})
            elif parts == ["api", "presets"]:
                self._json(HTTPStatus.OK, {"presets": self.manager.presets.list()})
            elif parts == ["api", "devices"]:
                self._json(HTTPStatus.OK, {"devices": self.manager.devices()})
            elif parts == ["api", "logs"]:
                limit = max(1, min(int(query.get('limit', ['500'])[0]), 2000))
                self._json(HTTPStatus.OK, {'logs': self.manager.events.read(
                    limit, query.get('gateway_id', [''])[0], query.get('level', [''])[0]),
                    'persistence_error': self.manager.events.persistence_error})
            elif parts == ["api", "settings"]:
                self._json(HTTPStatus.OK, self.manager.get_settings())
            elif parts == ["api", "diagnostics"]:
                self._json(HTTPStatus.OK, self.manager.diagnostics())
            elif len(parts) == 4 and parts[:2] == ["api", "ports"] and parts[3] == "logs":
                limit = max(1, min(int(query.get("limit", ["200"])[0]), 500))
                self._json(HTTPStatus.OK, {"logs": self.manager.logs(parts[2], limit)})
            elif parts and parts[0] == "api":
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            else:
                self._serve_static()
        except (ConfigError, ValueError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

    def do_POST(self) -> None:
        try:
            parts = self._parts()
            if parts == ["api", "presets"]:
                self._json(HTTPStatus.OK, {"preset": self.manager.presets.save(self._read_json())})
            elif parts == ["api", "settings", "telegram", "test"]:
                error = self.manager.notifier.test(self._read_json())
                self._json(HTTPStatus.OK, {"ok": error is None, "error": error})
            elif parts == ["api", "ports", "reorder"]:
                payload = self._read_json()
                ordered_ids = payload.get("ordered_ids")
                if not isinstance(ordered_ids, list) or not all(isinstance(item, str) for item in ordered_ids):
                    raise ConfigError("ordered_ids must be a list of gateway ids")
                self.manager.reorder_ports(ordered_ids)
                self._json(HTTPStatus.OK, {"ok": True})
            elif parts == ["api", "ports"]:
                port = self.manager.add_port(self._read_json())
                self._json(HTTPStatus.CREATED, {"port": port})
            elif len(parts) == 4 and parts[:2] == ["api", "ports"]:
                port_id, action = parts[2], parts[3]
                if action == "communication":
                    result = self.manager.communicate(port_id, self._read_json())
                    self._json(HTTPStatus.OK, result)
                elif action == "restart":
                    self.manager.restart_port(port_id)
                    self._json(HTTPStatus.OK, {"ok": True})
                elif action == "test":
                    result = self.manager.test_port(port_id)
                    self._json(HTTPStatus.OK, result)
                else:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
        except ConfigError as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

    def do_PUT(self) -> None:
        try:
            parts = self._parts()
            if parts == ["api", "settings"]:
                self._json(HTTPStatus.OK, self.manager.update_settings(self._read_json()))
            elif len(parts) == 3 and parts[:2] == ["api", "ports"]:
                port = self.manager.update_port(parts[2], self._read_json())
                self._json(HTTPStatus.OK, {"port": port})
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
        except ConfigError as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

    def do_DELETE(self) -> None:
        try:
            parts = self._parts()
            if len(parts) == 3 and parts[:2] == ["api", "presets"]:
                self.manager.presets.delete(parts[2])
                self._json(HTTPStatus.OK, {"ok": True})
                return
            if len(parts) == 3 and parts[:2] == ["api", "ports"]:
                self.manager.delete_port(parts[2])
                self._json(HTTPStatus.OK, {"ok": True})
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
        except ConfigError as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})


def main() -> None:
    config_path = os.environ.get("CONFIG_PATH", "/config/config.json")
    bind_host = os.environ.get("WEB_HOST", "0.0.0.0")
    bind_port = int(os.environ.get("WEB_PORT", "8098"))
    manager = GatewayManager(ConfigStore(config_path))
    manager.start()
    ApiHandler.manager = manager
    server = ThreadingHTTPServer((bind_host, bind_port), ApiHandler)
    server.daemon_threads = True

    stopped = threading.Event()

    def shutdown(_signum: int, _frame: object) -> None:
        if stopped.is_set():
            return
        stopped.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    print(f"USB Gateway UI listening on http://{bind_host}:{bind_port}", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
        manager.stop()


if __name__ == "__main__":
    main()
