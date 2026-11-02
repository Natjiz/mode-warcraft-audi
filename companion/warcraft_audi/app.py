from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
import threading

from .cache import AudioCache
from .client import CatalogProvider, Controller, RemoteProvider
from .capture import ScreenWatch
from .space_client import AutomaticProvider


class DesktopApp:
    def __init__(self, directory: Path, player=None, *, auto_start=True):
        self.directory, self.lock = Path(directory), threading.RLock()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.config_path = self.directory / 'settings.json'
        self.config = {'cache_mb': 300, 'server_url': '', 'volume': 80}
        if self.config_path.is_file():
            try:
                saved = json.loads(self.config_path.read_text(encoding='utf-8'))
                # Deliberately discard old manual calibration and any unknown keys.
                for key in self.config:
                    if key in saved:
                        self.config[key] = saved[key]
                self.config['cache_mb'] = min(2048, max(32, int(self.config['cache_mb'])))
                self.config['volume'] = min(100, max(0, int(self.config['volume'])))
            except (TypeError, ValueError, OSError):
                self.config = {'cache_mb': 300, 'server_url': '', 'volume': 80}
        self.cache = AudioCache(self.directory / 'audio', self.config['cache_mb'] * 1024**2)
        self.catalog = json.loads((Path(__file__).resolve().parents[1] / 'catalog.json').read_text(encoding='utf-8'))
        self.server_token = os.environ.get('WCA_SERVER_TOKEN', '')
        self.controller = Controller(self.cache, self.provider(), player)
        self.controller.player.volume = self.config['volume'] / 100
        self.watch = ScreenWatch(self)
        if auto_start:
            self.watch.start()

    def provider(self):
        if self.config['server_url']:
            return RemoteProvider(self.cache, self.config['server_url'], self.server_token)
        return AutomaticProvider(self.cache, self.catalog)

    def save(self):
        temporary = self.config_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.config, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(self.config_path)

    def state(self):
        with self.controller.lock, self.lock:
            return {**self.controller.state, 'running': self.watch.running.is_set(),
                    'connection': self.watch.connection, 'cache': self.cache.stats(),
                    'config': dict(self.config), 'has_token': bool(self.server_token)}

    def command(self, command, data=None):
        data = data or {}
        if command == 'start':
            self.watch.start()
            self.controller.status('Ожидаю игру и диалог квеста', 'idle')
        elif command == 'pause':
            self.watch.pause()
            self.controller.status('На паузе', 'idle')
        elif command == 'stop':
            self.controller.interrupt()
            self.controller.status('Остановлено; ожидаю следующий квест', 'idle')
        elif command == 'demo':
            self.controller.handle({'v': 1, 'action': 'demo'})
        elif command == 'clear':
            self.cache.clear()
        elif command == 'volume':
            volume = int(data['volume'])
            if not 0 <= volume <= 100:
                raise ValueError('Громкость: 0–100')
            with self.lock:
                self.config['volume'] = volume
                self.controller.player.volume = volume / 100
                self.save()
        elif command == 'settings':
            mb = int(data.get('cache_mb', self.config['cache_mb']))
            if not 32 <= mb <= 2048:
                raise ValueError('Кэш: 32–2048 МБ')
            url = str(data.get('server_url', '')).strip().rstrip('/')
            token = str(data.get('server_token', '')) or self.server_token
            if url:
                from urllib.parse import urlsplit
                parsed = urlsplit(url)
                if parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in {'127.0.0.1', 'localhost', '::1'}):
                    raise ValueError('Внешний сервер должен использовать HTTPS')
                provider = RemoteProvider(self.cache, url, token)
            else:
                provider = CatalogProvider(self.cache, self.catalog)
            # Preserve a deliberate pause; settings do not require a new Start click.
            active = self.watch.running.is_set()
            self.watch.pause()
            with self.lock:
                self.config.update(cache_mb=mb, server_url=url)
                self.server_token = token
                self.controller.provider = provider
                self.cache.limit_bytes = mb * 1024**2
                self.cache.prune()
                self.save()
            if active:
                self.watch.start()
            self.controller.status('Настройки сохранены', 'idle')
        else:
            raise ValueError('Неизвестная команда')

    def close(self):
        self.watch.pause()
        self.watch.closed.set()
        self.watch.running.set()
        self.watch.thread.join(timeout=3)
        self.controller.close()


def main():
    parser = argparse.ArgumentParser(description='Warcraft Audi desktop companion')
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--headless', action='store_true', help='Run without a window (development only)')
    args = parser.parse_args()
    directory = args.data_dir or Path(os.environ.get('LOCALAPPDATA', str(Path.home() / '.local/share'))) / 'WarcraftAudi'
    directory.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=directory / 'app.log', level=logging.ERROR, encoding='utf-8')
    instance = (directory / 'instance.lock').open('a+b')
    try:
        if os.name == 'nt':
            import msvcrt
            instance.seek(0); instance.write(b'0'); instance.flush(); instance.seek(0)
            msvcrt.locking(instance.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(instance.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        if os.name == 'nt' and not args.headless:
            from .native_ui import show_existing
            show_existing()
        instance.close()
        return
    app = None
    try:
        if os.name == 'nt':
            from .native_ui import enable_dpi_awareness
            enable_dpi_awareness()
        app = DesktopApp(directory)
        if args.headless:
            import signal
            stopped = threading.Event()
            signal.signal(signal.SIGINT, lambda *_: stopped.set())
            signal.signal(signal.SIGTERM, lambda *_: stopped.set())
            stopped.wait()
        else:
            from .native_ui import NativeWindow
            NativeWindow(app).run()
    except Exception:
        logging.exception('Companion failed')
        if os.name == 'nt':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, f'Не удалось запустить Warcraft Audi. Подробности: {directory / "app.log"}', 'Warcraft Audi', 0x10)
        else:
            raise
    finally:
        if app:
            app.close()
        instance.close()


if __name__ == '__main__':
    main()
