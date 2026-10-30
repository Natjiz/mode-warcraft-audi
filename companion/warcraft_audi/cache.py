"""Bounded, verified LRU storage. Playing files can be pinned against eviction."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import tempfile
import threading
import time


class AudioCache:
    def __init__(self, directory: Path, limit_bytes: int = 300 * 1024**2):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.limit_bytes = limit_bytes
        self.lock = threading.RLock()
        self.pinned = set()
        with self.db() as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS audio (key TEXT PRIMARY KEY, filename TEXT, size INTEGER, checksum TEXT, accessed REAL, request_key TEXT)")
            connection.execute("CREATE INDEX IF NOT EXISTS by_request ON audio(request_key)")
            for key, filename in connection.execute("SELECT key,filename FROM audio").fetchall():
                if not (self.directory / filename).is_file():
                    connection.execute("DELETE FROM audio WHERE key=?", (key,))
            retained = {row[0] for row in connection.execute("SELECT filename FROM audio")}
            for path in self.directory.iterdir():
                if (re.fullmatch(r"[0-9a-f]{64}\.(ogg|mp3|wav)", path.name) and path.name not in retained) or path.name.startswith(".wca-"):
                    path.unlink(missing_ok=True)
        self.prune()

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.directory / "cache.sqlite", timeout=15)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _check_key(self, key):
        if not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{64}", key):
            raise ValueError("Invalid audio key")

    def get(self, key: str) -> Path | None:
        self._check_key(key)
        with self.lock, self.db() as connection:
            row = connection.execute("SELECT filename,checksum FROM audio WHERE key=?", (key,)).fetchone()
            if not row:
                return None
            path = self.directory / row[0]
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != row[1]:
                connection.execute("DELETE FROM audio WHERE key=?", (key,))
                path.unlink(missing_ok=True)
                return None
            connection.execute("UPDATE audio SET accessed=? WHERE key=?", (time.time(), key))
            return path

    def find_request(self, request_key: str) -> tuple[str, Path] | None:
        with self.lock, self.db() as connection:
            rows = connection.execute("SELECT key FROM audio WHERE request_key=? ORDER BY accessed DESC", (request_key,)).fetchall()
        for (key,) in rows:
            path = self.get(key)
            if path:
                return key, path
        return None

    def put(self, key: str, data: bytes, checksum: str, mime: str, request_key: str = "") -> Path:
        self._check_key(key)
        if not data or len(data) > min(self.limit_bytes, 16 * 1024**2):
            raise ValueError("Запись превышает лимит кэша или пуста")
        if hashlib.sha256(data).hexdigest() != checksum:
            raise ValueError("Контрольная сумма загруженной записи не совпадает")
        if mime not in ("audio/ogg", "audio/mpeg", "audio/wav"):
            raise ValueError("Неподдерживаемый формат аудио")
        extension = {"audio/ogg": ".ogg", "audio/mpeg": ".mp3", "audio/wav": ".wav"}[mime]
        filename = key + extension
        with self.lock:
            previous = self.get(key)
            if previous and key in self.pinned:
                if hashlib.sha256(previous.read_bytes()).hexdigest() == checksum:
                    return previous
                raise ValueError("Запись сейчас воспроизводится")
            self.prune(reserve=max(0, len(data) - (previous.stat().st_size if previous else 0)), exclude={key})
            with self.db() as connection:
                used = connection.execute("SELECT COALESCE(SUM(size),0) FROM audio WHERE key<>?", (key,)).fetchone()[0]
                if used + len(data) > self.limit_bytes:
                    raise ValueError("Кэш заполнен воспроизводимыми записями")
                with tempfile.NamedTemporaryFile(dir=self.directory, prefix=".wca-", delete=False) as temporary:
                    temporary.write(data)
                    temporary_path = Path(temporary.name)
                try:
                    temporary_path.replace(self.directory / filename)
                finally:
                    temporary_path.unlink(missing_ok=True)
                if previous and previous.name != filename:
                    previous.unlink(missing_ok=True)
                connection.execute("INSERT OR REPLACE INTO audio VALUES (?,?,?,?,?,?)",
                                   (key, filename, len(data), checksum, time.time(), request_key))
        return self.directory / filename

    def pin(self, key: str):
        with self.lock:
            self.pinned.add(key)

    def unpin(self, key: str):
        with self.lock:
            self.pinned.discard(key)
            self.prune()

    def prune(self, reserve: int = 0, exclude: set | None = None):
        with self.lock, self.db() as connection:
            rows = connection.execute("SELECT key,filename,size FROM audio ORDER BY accessed ASC").fetchall()
            size = sum(row[2] for row in rows)
            for key, filename, length in rows:
                if size + reserve <= self.limit_bytes:
                    break
                if key in self.pinned or key in (exclude or set()):
                    continue
                try:
                    (self.directory / filename).unlink(missing_ok=True)
                except PermissionError:
                    continue
                connection.execute("DELETE FROM audio WHERE key=?", (key,))
                size -= length

    def clear(self):
        old = self.limit_bytes
        self.limit_bytes = 0
        try:
            self.prune()
        finally:
            self.limit_bytes = old

    def stats(self) -> dict:
        with self.lock, self.db() as connection:
            count, size = connection.execute("SELECT COUNT(*),COALESCE(SUM(size),0) FROM audio").fetchone()
        return {"count": count, "bytes": size, "limit_bytes": self.limit_bytes}
