from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import webbrowser

from .cache import AudioCache
from .client import CatalogProvider, Controller, RemoteProvider
from .protocol import Assembler, find_geometry, parse_frame, read_frame


class ScreenWatch:
    def __init__(self, app):
        self.app = app
        self.running, self.closed = threading.Event(), threading.Event()
        self.control_lock = threading.RLock()
        self.reset = True
        self.thread = threading.Thread(target=self.run, daemon=True, name="screen-reader")
        self.thread.start()

    def pause(self):
        with self.control_lock:
            self.running.clear()
            self.reset = True
            self.app.controller.interrupt()

    def start(self):
        with self.control_lock:
            self.reset = True
            self.running.set()

    def run(self):
        from PIL import Image
        import mss
        assembler, seen, lost = Assembler(), time.monotonic(), False
        while not self.closed.is_set():
            if not self.running.wait(0.2):
                continue
            if self.closed.is_set():
                break
            if self.reset:
                assembler, seen, lost = Assembler(), time.monotonic(), False
                self.reset = False
            try:
                with mss.mss() as capture:
                    while self.running.is_set() and not self.closed.is_set():
                        with self.app.lock:
                            region = dict(self.app.config.get("region", {}))
                            geometry = tuple(self.app.config.get("geometry", []))
                        if not region or len(geometry) != 4:
                            self.app.controller.status("Сначала выделите индикатор", "idle")
                            self.running.clear()
                            break
                        try:
                            shot = capture.grab(region)
                            image = Image.frombytes("RGB", shot.size, shot.rgb)
                            packet = read_frame(image, geometry)
                            with self.control_lock:
                                if not self.running.is_set() or self.closed.is_set():
                                    break
                                if self.reset:
                                    assembler = Assembler()
                                    self.reset = False
                                previous = assembler.identity
                                message = assembler.feed(packet)
                                if previous != assembler.identity:
                                    self.app.controller.interrupt()
                                    self.app.controller.status("Получаю текст квеста", "loading")
                                seen, lost = time.monotonic(), False
                                if message:
                                    self.app.controller.handle(message)
                        except (ValueError, OSError):
                            with self.control_lock:
                                if self.running.is_set() and time.monotonic() - seen > 2 and not lost:
                                    self.app.controller.interrupt()
                                    self.app.controller.status("Индикатор не виден. Откройте игру и не закрывайте его другими окнами.", "idle")
                                    lost = True
                        self.closed.wait(0.04)
            except Exception:
                self.app.controller.interrupt()
                self.app.controller.status("Не удалось получить изображение экрана. Проверьте разрешение на захват и режим игры.", "error")
                self.running.clear()


class DesktopApp:
    def __init__(self, directory: Path, player=None):
        self.directory, self.lock = Path(directory), threading.RLock()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.config_path = self.directory / "settings.json"
        self.config = {"cache_mb": 300, "server_url": "", "volume": 80}
        if self.config_path.is_file():
            try:
                self.config.update(json.loads(self.config_path.read_text(encoding="utf-8")))
            except (ValueError, OSError):
                pass
        self.cache = AudioCache(self.directory / "audio", int(self.config["cache_mb"]) * 1024**2)
        catalog_path = Path(__file__).resolve().parents[1] / "catalog.json"
        self.catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        self.server_token = os.environ.get("WCA_SERVER_TOKEN", "")
        self.controller = Controller(self.cache, self.provider(), player)
        self.controller.player.volume = self.config["volume"] / 100
        self.watch = ScreenWatch(self)
        self.capture_id, self.capture_image, self.capture_bounds = None, None, None
        self.ui_token = secrets.token_urlsafe(32)

    def provider(self):
        if self.config["server_url"]:
            return RemoteProvider(self.cache, self.config["server_url"], self.server_token)
        return CatalogProvider(self.cache, self.catalog)

    def save(self):
        temporary = self.config_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.config, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.config_path)

    def state(self):
        with self.controller.lock, self.lock:
            return {**self.controller.state, "running": self.watch.running.is_set(), "cache": self.cache.stats(),
                    "config": dict(self.config), "has_token": bool(self.server_token)}

    def screenshot(self):
        from PIL import Image
        import mss
        self.watch.pause()
        self.controller.status("Переключитесь в WoW. Снимок будет сделан через 3 секунды.", "idle")
        time.sleep(3)  # Let the user bring the game in front of the browser window.
        with mss.mss() as capture:
            bounds = dict(capture.monitors[0])
            shot = capture.grab(bounds)
        if shot.width * shot.height > 32000000:
            raise ValueError("Слишком большое изображение. Временно оставьте один монитор.")
        image = Image.frombytes("RGB", shot.size, shot.rgb)
        with self.lock:
            self.capture_id = secrets.token_hex(8)
            self.capture_image, self.capture_bounds = image, bounds
        output = io.BytesIO()
        image.save(output, "PNG")
        return output.getvalue(), self.capture_id

    def calibrate(self, value):
        with self.lock:
            if value.get("capture_id") != self.capture_id or self.capture_image is None:
                raise ValueError("Изображение устарело. Снимите экран ещё раз.")
            x, y, width, height = (int(value[k]) for k in ("x", "y", "width", "height"))
            image = self.capture_image
            if width < 20 or height < 20 or x < 0 or y < 0 or x + width > image.width or y + height > image.height:
                raise ValueError("Обведите весь индикатор вместе с цветными углами")
            crop = image.crop((x, y, x + width, y + height))
            geometry = find_geometry(crop)
            read_frame(crop, geometry)  # Require a valid checksummed frame, not just three colors.
            self.config["region"] = {"left": self.capture_bounds["left"] + x,
                "top": self.capture_bounds["top"] + y, "width": width, "height": height}
            self.config["geometry"] = list(geometry)
            self.save()
        self.controller.status("Индикатор найден. Нажмите «Запустить».", "idle")

    def command(self, command, data):
        if command == "start":
            if "region" not in self.config:
                raise ValueError("Сначала выделите индикатор аддона")
            self.watch.start()
            self.controller.status("Ожидаю квест в игре", "idle")
        elif command == "pause":
            self.watch.pause()
            self.controller.status("На паузе", "idle")
        elif command == "demo":
            self.watch.pause()
            self.controller.handle({"v": 1, "action": "demo"})
        elif command == "calibrate":
            self.calibrate(data)
        elif command == "clear":
            self.cache.clear()
        elif command == "settings":
            mb, volume = int(data.get("cache_mb", 300)), int(data.get("volume", 80))
            if not 32 <= mb <= 2048 or not 0 <= volume <= 100:
                raise ValueError("Кэш: 32–2048 МБ; громкость: 0–100")
            url = str(data.get("server_url", "")).strip().rstrip("/")
            token = str(data.get("server_token", ""))
            provider = RemoteProvider(self.cache, url, token) if url else CatalogProvider(self.cache, self.catalog)
            # Validate scheme before preserving the URL; no request or key is needed here.
            if url:
                from urllib.parse import urlsplit
                parsed = urlsplit(url)
                if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}):
                    raise ValueError("Внешний сервер должен использовать HTTPS")
            self.watch.pause()
            with self.lock:
                self.config.update(cache_mb=mb, volume=volume, server_url=url)
                if token:
                    self.server_token = token
                self.controller.provider = self.provider()
                self.controller.player.volume = volume / 100
                self.cache.limit_bytes = mb * 1024**2
                self.cache.prune()
                self.save()  # Authentication token deliberately stays in memory only.
            self.controller.status("Настройки сохранены. Нажмите «Запустить».", "idle")
        else:
            raise ValueError("Неизвестная команда")

    def close(self):
        self.watch.pause()
        self.watch.closed.set()
        self.watch.running.set()
        self.watch.thread.join(timeout=3)
        self.controller.close()


def make_ui(app, port=0):
    html = (Path(__file__).with_name("ui.html")).read_text(encoding="utf-8").replace("__TOKEN__", app.ui_token)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send_data(self, status, data, mime="application/json; charset=utf-8", extra=None):
            if not isinstance(data, bytes):
                data = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' blob:; frame-ancestors 'none'")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(data)

        def allowed(self):
            expected = f"127.0.0.1:{self.server.server_port}"
            origin = self.headers.get("Origin")
            if self.headers.get("Host") != expected or (origin and origin != "http://" + expected):
                self.send_data(403, {"error": "Недопустимый источник запроса"})
                return False
            if self.path != "/" and self.headers.get("X-WCA-Token") != app.ui_token:
                self.send_data(403, {"error": "Перезагрузите страницу программы"})
                return False
            return True

        def do_GET(self):
            if not self.allowed():
                return
            try:
                if self.path == "/":
                    return self.send_data(200, html.encode(), "text/html; charset=utf-8")
                if self.path == "/api/state":
                    return self.send_data(200, app.state())
                if self.path == "/api/capture":
                    data, capture_id = app.screenshot()
                    return self.send_data(200, data, "image/png", {"X-Capture-ID": capture_id})
                self.send_data(404, {"error": "Не найдено"})
            except Exception as exc:
                self.send_data(400, {"error": str(exc)})

        def do_POST(self):
            if not self.allowed():
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                if not 0 <= length <= 8192:
                    raise ValueError("Слишком большой запрос")
                value = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(value, dict):
                    raise ValueError("Некорректный запрос")
                if not self.path.startswith("/api/"):
                    return self.send_data(404, {"error": "Не найдено"})
                if self.path == "/api/quit":
                    self.send_data(200, {"ok": True})
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                    return
                app.command(self.path[5:], value)
                self.send_data(200, {"ok": True})
            except (ValueError, OSError) as exc:
                self.send_data(400, {"error": str(exc)})

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main():
    parser = argparse.ArgumentParser(description="Warcraft Audi desktop companion")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if os.name == "nt":
        import ctypes
        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(-4)
        except (AttributeError, OSError):
            pass
    directory = args.data_dir or Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local/share"))) / "WarcraftAudi"
    directory.mkdir(parents=True, exist_ok=True)
    instance = (directory / "instance.lock").open("a+b")
    try:
        if os.name == "nt":
            import msvcrt
            instance.seek(0)
            instance.write(b"0")
            instance.flush()
            instance.seek(0)
            msvcrt.locking(instance.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(instance.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        active = directory / "active-url.txt"
        if active.is_file():
            url = active.read_text().strip()
            import re
            if re.fullmatch(r"http://127\.0\.0\.1:\d+/", url) and not args.no_browser:
                webbrowser.open(url)
        print("Warcraft Audi уже запущен.", flush=True)
        instance.close()
        return
    app = DesktopApp(directory)
    server = make_ui(app, args.port)
    url = f"http://127.0.0.1:{server.server_port}/"
    print("Warcraft Audi — программа запущена. Это окно можно свернуть.", flush=True)
    print(url, flush=True)
    active_url = directory / "active-url.txt"
    active_url.write_text(url)
    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.close()
        server.server_close()
        active_url.unlink(missing_ok=True)
        instance.close()


if __name__ == "__main__":
    main()
