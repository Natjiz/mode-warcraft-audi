"""Automatic screen discovery; positions never need to be chosen or saved."""
from __future__ import annotations

import math
import threading
import time
from .protocol import Assembler, WIDTH, HEIGHT, find_geometry, read_frame


class AutoLocator:
    def __init__(self):
        self.region = self.geometry = None
        self.next_scan = 0.0

    def reset(self):
        self.region = self.geometry = None
        self.next_scan = 0.0

    @staticmethod
    def image(shot):
        from PIL import Image
        return Image.frombytes('RGB', shot.size, shot.rgb)

    def read(self, capture, now=None):
        now = time.monotonic() if now is None else now
        if self.region:
            try:
                return read_frame(self.image(capture.grab(self.region)), self.geometry)
            except (OSError, ValueError):
                self.region = self.geometry = None
        if now < self.next_scan:
            return None
        self.next_scan = now + 0.8
        # Refresh topology on every discovery pass: monitor/DPI changes are normal.
        if hasattr(capture, "_monitors"):
            capture._monitors = []  # mss 10 caches topology; refresh after a screen change.
        monitors = capture.monitors
        for monitor in monitors[1:] or monitors:
            if monitor['width'] * monitor['height'] > 40000000:
                continue
            try:
                image = self.image(capture.grab(monitor))
                x, y, sx, sy = find_geometry(image)
                packet = read_frame(image, (x, y, sx, sy))
                left, top = max(0, math.floor(x)), max(0, math.floor(y))
                width = min(image.width - left, math.ceil(x + WIDTH * sx) - left)
                height = min(image.height - top, math.ceil(y + HEIGHT * sy) - top)
                self.region = dict(left=monitor['left'] + left, top=monitor['top'] + top,
                                   width=width, height=height)
                self.geometry = x - left, y - top, sx, sy
                return packet
            except (OSError, ValueError):
                continue
        return None


class ScreenWatch:
    def __init__(self, app):
        self.app = app
        self.running, self.closed = threading.Event(), threading.Event()
        self.control_lock = threading.RLock()
        self.generation = 0
        self.connection = 'waiting'
        self.thread = threading.Thread(target=self.run, daemon=True, name='screen-reader')
        self.thread.start()

    def pause(self):
        with self.control_lock:
            self.running.clear()
            self.generation += 1
            self.connection = 'paused'
            self.app.controller.interrupt()

    def start(self):
        with self.control_lock:
            self.generation += 1
            self.connection = 'waiting'
            self.running.set()

    def run(self):
        import mss
        assembler, locator = Assembler(), AutoLocator()
        generation, seen, lost = -1, None, False
        while not self.closed.is_set():
            if not self.running.wait(0.2):
                continue
            if self.closed.is_set():
                break
            try:
                with mss.mss() as capture:
                    while self.running.is_set() and not self.closed.is_set():
                        with self.control_lock:
                            epoch = self.generation
                            if generation != epoch:
                                generation = epoch
                                assembler, locator = Assembler(), AutoLocator()
                                seen, lost = None, False
                        packet = locator.read(capture)
                        with self.control_lock:
                            # A capture started before pause/settings must never restart audio.
                            if epoch != self.generation or not self.running.is_set() or self.closed.is_set():
                                continue
                            if packet:
                                previous = assembler.identity
                                message = assembler.feed(packet)
                                if previous != assembler.identity:
                                    self.app.controller.interrupt()
                                    self.app.controller.status('Получаю текст квеста', 'loading')
                                self.connection = 'connected'
                                seen, lost = time.monotonic(), False
                                if message:
                                    self.app.controller.handle(message)
                            elif seen is not None and time.monotonic() - seen > 2 and not lost:
                                self.app.controller.interrupt()
                                self.app.controller.status('Ожидаю игру и следующий квест', 'idle')
                                self.connection = 'waiting'
                                lost = True
                        self.closed.wait(0.04)
            except Exception:
                with self.control_lock:
                    if self.running.is_set() and not self.closed.is_set():
                        self.app.controller.interrupt()
                        self.app.controller.status('Не удалось прочитать экран. Используйте оконный режим без рамки.', 'error')
                        self.connection = 'capture_error'
                # Retry automatically, including when WoW was launched after this program.
                self.closed.wait(1)
