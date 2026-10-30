from __future__ import annotations

import json
from pathlib import Path
import re
import threading
import time
import urllib.error
import urllib.parse

from .network import fetch, fetch_json
from .protocol import canonical_request, request_key
from .service import Catalog


class Cancelled(Exception):
    pass


CATALOG_URL = "https://raw.githubusercontent.com/Natjiz/mode-warcraft-audi/main/companion/catalog.json"


class CatalogProvider:
    def __init__(self, cache, catalog: dict):
        self.cache, self.catalog = cache, Catalog(catalog)
        self.refreshed = 0.0

    def get_audio(self, request, cancelled, status):
        entry = self.catalog.find(request)
        if not entry and time.monotonic() - self.refreshed > 300:
            self.refreshed = time.monotonic()
            try:
                self.catalog = Catalog(fetch_json(CATALOG_URL, timeout=10))
                entry = self.catalog.find(request)
            except (OSError, ValueError):
                pass
        key = request_key(request, self.catalog.revision)
        cached = self.cache.get(key)
        if cached:
            return key, cached
        if not entry:
            previous = self.cache.find_request(request_key(request))
            if previous:
                status("Воспроизвожу сохранённую запись")
                return previous
            raise ValueError("Готовой записи пока нет. Подключите сервер генерации в настройках программы.")
        status("Скачиваю готовую запись")
        data, _ = fetch(entry["url"])
        if cancelled.is_set():
            raise Cancelled()
        return key, self.cache.put(key, data, entry["sha256"], entry["mime"], request_key(request))


class RemoteProvider:
    def __init__(self, cache, base_url: str, token=""):
        self.cache, self.base_url, self.token = cache, base_url.rstrip("/"), token
        parsed = urllib.parse.urlsplit(self.base_url)
        if parsed.query or parsed.fragment or parsed.username or parsed.password or parsed.path not in {"", "/"}:
            raise ValueError("Укажите только HTTPS-адрес сервера, без ключей, пути и параметров")

    def get_audio(self, request, cancelled, status):
        try:
            job = fetch_json(self.base_url + "/v1/quests", payload=request, token=self.token, timeout=10)
        except urllib.error.HTTPError:
            raise
        except OSError:
            previous = self.cache.find_request(request_key(request))
            if previous:
                status("Сервер недоступен; использую кэш")
                return previous
            raise
        started = time.monotonic()
        while job.get("status") in {"queued", "generating"}:
            status("Ожидание в очереди" if job["status"] == "queued" else "Генерирую озвучку; первый запрос может занять несколько минут")
            if cancelled.wait(0.5):
                raise Cancelled()
            if time.monotonic() - started > 900:
                raise ValueError("Сервер не успел подготовить запись за 15 минут")
            key = job.get("id", "")
            if not re.fullmatch(r"[0-9a-f]{64}", key):
                raise ValueError("Некорректный ID запроса сервера")
            job = fetch_json(self.base_url + "/v1/jobs/" + key, token=self.token)
        if cancelled.is_set():
            raise Cancelled()
        if job.get("status") != "ready":
            raise ValueError(job.get("message", "Запись не подготовлена"))
        key = job["id"]
        path = self.cache.get(key)
        if path:
            return key, path
        audio = job["audio"]
        if audio.get("url") != "/v1/audio/" + key:
            raise ValueError("Сервер вернул неожиданный адрес аудио")
        status("Скачиваю запись")
        data, _ = fetch(self.base_url + audio["url"], token=self.token)
        if cancelled.is_set():
            raise Cancelled()
        return key, self.cache.put(key, data, audio["sha256"], audio["mime"], request_key(request))


class AudioPlayer:
    def __init__(self):
        self.lock = threading.RLock()
        self.device, self.source = None, None
        self.volume = 0.8
        self.serial = 0

    def stop(self):
        with self.lock:
            self.serial += 1
            if self.device:
                self.device.close()
                self.device = None
            if self.source:
                self.source.close()
                self.source = None

    def play(self, path, completed):
        import miniaudio
        from array import array
        self.stop()
        with self.lock:
            serial = self.serial
            self.source = miniaudio.stream_file(str(path), output_format=miniaudio.SampleFormat.SIGNED16, nchannels=2, sample_rate=48000)
            source = self.source
            finished = threading.Event()

            def volume_stream():
                try:
                    frames = yield b""
                    while True:
                        samples = source.send(frames)
                        if self.volume != 1:
                            samples = array("h", (int(sample * self.volume) for sample in samples))
                        frames = yield samples
                except StopIteration:
                    return
                finally:
                    finished.set()

            stream = volume_stream()
            next(stream)
            self.device = miniaudio.PlaybackDevice(output_format=miniaudio.SampleFormat.SIGNED16, nchannels=2, sample_rate=48000)
            self.device.start(stream)

        def wait():
            finished.wait()
            with self.lock:
                if serial != self.serial:
                    return
                self.stop()
            completed()
        threading.Thread(target=wait, daemon=True).start()


class Controller:
    def __init__(self, cache, provider, player=None):
        from concurrent.futures import ThreadPoolExecutor
        self.cache, self.provider, self.player = cache, provider, player or AudioPlayer()
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="audio-fetch")
        self.lock, self.cancelled = threading.RLock(), threading.Event()
        self.epoch, self.pinned, self.future = 0, None, None
        self.state = {"phase": "idle", "message": "Выделите индикатор аддона и нажмите «Запустить»", "quest": ""}

    def status(self, message, phase=None):
        with self.lock:
            self.state["message"] = message
            if phase:
                self.state["phase"] = phase

    def interrupt(self):
        with self.lock:
            self.epoch += 1
            self.cancelled.set()
            if self.future:
                self.future.cancel()
            self.player.stop()
            if self.pinned:
                self.cache.unpin(self.pinned)
                self.pinned = None

    def handle(self, message):
        self.interrupt()
        if message.get("action") == "stop":
            self.status("Остановлено", "idle")
            return
        if message.get("action") not in {"play", "select", "demo"}:
            return
        try:
            request = canonical_request(message)
        except ValueError as exc:
            self.status(str(exc), "error")
            return
        with self.lock:
            epoch = self.epoch
            cancelled = self.cancelled = threading.Event()
            self.state["quest"] = str(request["quest_id"]) + " · " + request["speaker"]
            self.status("Ищу запись", "loading")
            provider = self.provider

        def update(text):
            with self.lock:
                if self.epoch == epoch:
                    self.status(text, "loading")

        def worker():
            try:
                key, path = provider.get_audio(request, cancelled, update)
                with self.lock:
                    if cancelled.is_set() or epoch != self.epoch:
                        return
                    if message.get("action") == "select":
                        self.status("Запись готова", "idle")
                        return
                    self.cache.pin(key)
                    self.pinned = key

                    def done():
                        with self.lock:
                            if epoch == self.epoch:
                                self.cache.unpin(key)
                                self.pinned = None
                                self.status("Воспроизведение завершено", "idle")
                    self.player.play(path, done)
                    self.status("Воспроизвожу", "playing")
            except Cancelled:
                pass
            except Exception as exc:
                with self.lock:
                    if epoch == self.epoch:
                        if self.pinned:
                            self.cache.unpin(self.pinned)
                            self.pinned = None
                        self.status(str(exc), "error")
        self.future = self.executor.submit(worker)

    def close(self):
        self.interrupt()
        self.executor.shutdown(wait=False, cancel_futures=True)
