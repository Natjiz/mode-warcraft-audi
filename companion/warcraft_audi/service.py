"""Single-flight generation queue shared by HTTP service and catalog client."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import threading
import time

from .cache import AudioCache
from .network import fetch
from .protocol import canonical_request, request_key


def catalog_key(request: dict) -> str:
    fields = {key: request[key] for key in ("quest_id", "stage", "locale", "gender", "npc_id", "text")}
    return request_key(fields)


class Catalog:
    def __init__(self, data: dict):
        if data.get("schema_version") != 1 or not isinstance(data.get("items"), list):
            raise ValueError("Неподдерживаемый каталог")
        self.revision = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
        self.entries = {}
        for entry in data["items"]:
            normalized = canonical_request(entry["request"])
            key = catalog_key(normalized)
            if key in self.entries:
                raise ValueError("Повторяющаяся запись каталога")
            if not isinstance(entry.get("sha256"), str) or len(entry["sha256"]) != 64:
                raise ValueError("Нет контрольной суммы записи")
            self.entries[key] = entry

    def find(self, request):
        return self.entries.get(catalog_key(request)) or self.entries.get(catalog_key({**request, "gender": "unknown"}))


class VoiceService:
    def __init__(self, directory: Path, catalog: Catalog, engine=None, limit_bytes=10 * 1024**3, queue_limit=16):
        self.cache = AudioCache(directory, limit_bytes)
        self.catalog, self.engine, self.queue_limit = catalog, engine, queue_limit
        self.revision = hashlib.sha256((catalog.revision + getattr(engine, "revision", "catalog-only")).encode()).hexdigest()
        self.jobs, self.lock = {}, threading.RLock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="voice-worker")

    def close(self):
        self.executor.shutdown(wait=True, cancel_futures=True)

    def info(self):
        return {"version": "0.4.0", "voice_revision": self.revision, "generation": self.engine is not None, "locales": ["ruRU"]}

    def submit(self, payload: dict) -> dict:
        request = canonical_request(payload)
        if request["locale"] != "ruRU":
            raise ValueError("Первая версия поддерживает русский язык клиента")
        key = request_key(request, self.revision)
        with self.lock:
            with self.cache.lock:
                existing = self.cache.get(key)
                if existing:
                    return self._ready(key, existing)
            if key in self.jobs:
                job = self.jobs[key]
                if job["status"] in {"queued", "generating"} or (job["status"] in {"failed", "missing"} and time.monotonic() - job["updated"] < 30):
                    return self._public(job)
            pending = sum(job["status"] in {"queued", "generating"} for job in self.jobs.values())
            if pending >= self.queue_limit:
                raise ValueError("Очередь сервера заполнена. Повторите позже.")
            for expired in [k for k, v in self.jobs.items() if v["status"] not in {"queued", "generating"} and time.monotonic() - v["updated"] > 300]:
                self.jobs.pop(expired)
            if len(self.jobs) >= 256:
                completed = sorted((k for k in self.jobs if self.jobs[k]["status"] not in {"queued", "generating"}), key=lambda k: self.jobs[k]["updated"])
                for old in completed[:len(self.jobs) - 255]:
                    self.jobs.pop(old)
            self.jobs[key] = {"id": key, "status": "queued", "updated": time.monotonic()}
            self.executor.submit(self._generate, key, request)
            return self._public(self.jobs[key])

    def _public(self, job):
        return {key: value for key, value in job.items() if key != "updated"}

    def _ready(self, key, path):
        mime = {".mp3": "audio/mpeg", ".ogg": "audio/ogg", ".wav": "audio/wav"}[path.suffix]
        return {"id": key, "status": "ready", "audio": {"url": "/v1/audio/" + key,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "size": path.stat().st_size, "mime": mime}}

    def status(self, key):
        with self.cache.lock:
            path = self.cache.get(key)
            if path:
                return self._ready(key, path)
        with self.lock:
            if key not in self.jobs:
                raise KeyError("Запрос не найден")
            if self.jobs[key]["status"] == "ready":
                return {"id": key, "status": "expired", "message": "Запись вытеснена из кэша сервера. Повторите запрос."}
            return self._public(self.jobs[key])

    def _generate(self, key, request):
        def update(status, **values):
            with self.lock:
                self.jobs[key] = {"id": key, "status": status, "updated": time.monotonic(), **values}
        update("generating")
        try:
            entry = self.catalog.find(request)
            if entry:
                data, _ = fetch(entry["url"])
                checksum, mime = entry["sha256"], entry["mime"]
            elif self.engine:
                data, mime = self.engine.generate(request)
                checksum = hashlib.sha256(data).hexdigest()
            else:
                update("missing", message="Готовой записи ещё нет. Для новых квестов подключите сервер генерации.")
                return
            self.cache.put(key, data, checksum, mime, request_key(request))
            update("ready")
        except Exception as exc:
            # Do not put provider URLs, credentials or quest text into the public error.
            update("failed", message="Не удалось подготовить запись. Проверьте журнал сервера.", error_type=type(exc).__name__)
            import logging
            logging.getLogger(__name__).error("Voice job %s failed (%s)", key[:12], type(exc).__name__)
