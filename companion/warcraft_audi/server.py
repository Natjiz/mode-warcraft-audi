from __future__ import annotations

import argparse
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re

from .service import Catalog, VoiceService


def make_server(service, host="127.0.0.1", port=8765, token=""):
    if host not in {"127.0.0.1", "localhost", "::1"} and not token:
        raise ValueError("Для внешнего сервера задайте WCA_SERVER_TOKEN")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send_json(self, status, value):
            data = json.dumps(value, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def authorized(self):
            if token and not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + token):
                self.send_json(401, {"error": "Нужен ключ сервера"})
                return False
            return True

        def do_GET(self):
            if self.path == "/health":
                return self.send_json(200, {"status": "ok"})
            if not self.authorized():
                return
            if self.path == "/v1/info":
                return self.send_json(200, service.info())
            match = re.fullmatch(r"/v1/(jobs|audio)/([0-9a-f]{64})", self.path)
            if not match:
                return self.send_json(404, {"error": "Не найдено"})
            kind, key = match.groups()
            try:
                if kind == "jobs":
                    return self.send_json(200, service.status(key))
                with service.cache.lock:
                    path = service.cache.get(key)
                    if not path:
                        raise KeyError()
                    service.cache.pin(key)
                try:
                    data = path.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", service._ready(key, path)["audio"]["mime"])
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                finally:
                    service.cache.unpin(key)
            except KeyError:
                self.send_json(404, {"error": "Запись не найдена"})

        def do_POST(self):
            if not self.authorized():
                return
            if self.path != "/v1/quests":
                return self.send_json(404, {"error": "Не найдено"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 32768:
                    return self.send_json(413, {"error": "Недопустимый размер текста"})
                value = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(value, dict):
                    raise ValueError("Ожидается объект запроса")
                self.send_json(202, service.submit(value))
            except (ValueError, UnicodeError) as exc:
                self.send_json(400, {"error": str(exc)})

    return ThreadingHTTPServer((host, port), Handler)


def main():
    parser = argparse.ArgumentParser(description="Warcraft Audi voice service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--cache", type=Path, default=Path(".wca-server-cache"))
    parser.add_argument("--catalog", type=Path, default=Path("companion/catalog.json"))
    parser.add_argument("--engine", choices=("catalog", "qwen"), default="catalog")
    parser.add_argument("--models", type=Path, default=Path("/workspace/.warcraft-audi-models"))
    args = parser.parse_args()
    engine = None
    if args.engine == "qwen":
        from .voice_engine import QwenEngine
        engine = QwenEngine(args.models, Path("voices"))
    service = VoiceService(args.cache, Catalog(json.loads(args.catalog.read_text(encoding="utf-8"))), engine)
    server = make_server(service, args.host, args.port, os.environ.get("WCA_SERVER_TOKEN", ""))
    print(f"Voice service listening on {args.host}:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        service.close()


if __name__ == "__main__":
    main()
