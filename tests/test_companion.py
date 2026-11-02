from pathlib import Path
import hashlib
import io
import json
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from companion.warcraft_audi.protocol import *
from companion.warcraft_audi.cache import AudioCache
from companion.warcraft_audi.service import Catalog, VoiceService
from companion.warcraft_audi.server import make_server
from companion.warcraft_audi.client import Controller, RemoteProvider

try:
    from lupa.lua51 import LuaRuntime
    from PIL import Image, ImageDraw
except ImportError:
    LuaRuntime = None


class ProtocolTests(unittest.TestCase):
    def test_pages_reordered_duplicated_and_corrupted(self):
        value = {"v": 1, "action": "play", "text": "Русский текст\n" * 100}
        data = json.dumps(value, ensure_ascii=False).encode()
        packets = [frame_packet(data, 7, 2, i) for i in range((len(data) + PAYLOAD - 1) // PAYLOAD)]
        reader = Assembler()
        self.assertIsNone(reader.feed(packets[-1]))
        self.assertIsNone(reader.feed(packets[-1]))
        completed = None
        for packet in packets[:-1]:
            completed = reader.feed(packet) or completed
        self.assertEqual(completed, value)
        self.assertIsNone(reader.feed(packets[0]))
        corrupt = bytearray(packets[0]); corrupt[24] ^= 1
        with self.assertRaises(ValueError):
            reader.feed(bytes(corrupt))

    def test_late_old_request_does_not_interrupt_new_message(self):
        reader = Assembler()
        data = json.dumps({"v": 1, "action": "stop"}).encode()
        self.assertIsNotNone(reader.feed(frame_packet(data, 7, 10, 0)))
        self.assertIsNone(reader.feed(frame_packet(data, 7, 9, 0)))
        self.assertEqual(reader.identity, (7, 10))

    def test_personal_name_removed_before_remote_request_and_text_changes_key(self):
        source = {"quest_id": 50913, "stage": "detail", "locale": "ruRU", "npc_id": 128152,
                  "gender": "male", "text": "Ты достоин моего благословения, Тестовыйгерой.", "player_name": "Тестовыйгерой"}
        request = canonical_request(source)
        self.assertEqual(request["text"], "Ты достоин моего благословения.")
        self.assertNotIn("player_name", request)
        self.assertNotEqual(request_key(request), request_key({**request, "text": "Другой текст"}))

    @unittest.skipIf(LuaRuntime is None, "Lupa/Pillow required")
    def test_actual_lua_pixels_roundtrip_into_python_at_multiple_scales(self):
        lua = LuaRuntime(unpack_returned_tuples=True)
        lua.execute((ROOT / "tests/wow_mock.lua").read_text())
        lua.execute("namespace = {}")
        for name in ("Core.lua", "UI.lua", "Bridge.lua"):
            lua.execute("return function(...)\n" + (ROOT / "addon/WarcraftAudi" / name).read_text() + "\nend")("WarcraftAudi", lua.globals().namespace)
        lua.execute('fire("ADDON_LOADED", "WarcraftAudi"); mock.text = string.rep("Привет, путник! ", 60); fire("QUEST_DETAIL")')
        bridge = lua.globals().WarcraftAudi.bridge
        reader = Assembler()
        completed = None
        for page in range(int(bridge.count)):
            bridge.page = page
            bridge.Draw(bridge)
            image = Image.new("RGB", (WIDTH, HEIGHT), "black")
            draw = ImageDraw.Draw(image)
            for i in range(2, len(bridge.textures) + 1):
                texture = bridge.textures[i]
                x, y = int(texture.point[2]), -int(texture.point[3])
                draw.rectangle((x, y, x + int(texture.width) - 1, y + int(texture.height) - 1),
                    fill=tuple(round(texture.color[j] * 255) for j in (1, 2, 3)))
            packet = read_frame(image, find_geometry(image))
            for scale in (0.75, 1.0, 1.5):
                resized = image.resize((round(WIDTH * scale), round(HEIGHT * scale)), Image.Resampling.BILINEAR)
                canvas = Image.new("RGB", (resized.width + 30, resized.height + 30), (20, 20, 20))
                canvas.paste(resized, (12, 9))
                self.assertEqual(read_frame(canvas, find_geometry(canvas)), packet)
            completed = reader.feed(packet) or completed
        self.assertEqual(completed["action"], "play")
        self.assertEqual(completed["text"], "Привет, путник! " * 60)
        self.assertEqual(len(lua.globals().mock.calls), 0, "Companion mode must not play duplicate in-game audio")


class CacheTests(unittest.TestCase):
    def test_owned_orphan_files_are_removed_on_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            orphan = root / ("f" * 64 + ".ogg")
            orphan.write_bytes(b"orphan")
            personal = root / "keep.txt"; personal.write_text("not owned by the cache")
            AudioCache(root, 10)
            self.assertFalse(orphan.exists())
            self.assertTrue(personal.exists())

    def test_lru_integrity_and_pinning(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = AudioCache(Path(directory), 10)
            def put(character):
                data = character.encode() * 4
                key = hashlib.sha256(data).hexdigest()
                cache.put(key, data, hashlib.sha256(data).hexdigest(), "audio/ogg")
                return key
            first = put("a"); cache.pin(first)
            second = put("b"); third = put("c")
            self.assertIsNotNone(cache.get(first)); self.assertIsNone(cache.get(second)); self.assertIsNotNone(cache.get(third))
            self.assertLessEqual(cache.stats()["bytes"], 10)
            cache.unpin(first); cache.clear()
            self.assertEqual(cache.stats()["bytes"], 0)
            with self.assertRaises(ValueError):
                cache.put("0" * 64, b"data", "1" * 64, "audio/ogg")

    def test_corrupt_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = AudioCache(Path(directory), 100)
            data = b"test"; key = hashlib.sha256(data).hexdigest()
            path = cache.put(key, data, key, "audio/mpeg")
            path.write_bytes(b"corrupted")
            self.assertIsNone(cache.get(key))


class ServiceTests(unittest.TestCase):
    def test_a_late_screen_capture_cannot_restart_after_pause(self):
        from companion.warcraft_audi.app import DesktopApp
        from types import SimpleNamespace
        captured, release = threading.Event(), threading.Event()
        packet = frame_packet(json.dumps({"v": 1, "action": "demo"}).encode(), 7, 1, 0)
        image = render_frame(packet)
        class Screen:
            monitors = [{"left":0,"top":0,"width":WIDTH,"height":HEIGHT}]
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def grab(self, region):
                captured.set(); release.wait(2)
                return SimpleNamespace(size=image.size, rgb=image.tobytes())
        class Player:
            volume = 1
            def stop(self): pass
        with tempfile.TemporaryDirectory() as directory, patch('mss.mss', Screen):
            app = DesktopApp(Path(directory), Player(), auto_start=False)
            try:
                with patch.object(app.controller, 'handle') as handle:
                    app.command('start', {})
                    self.assertTrue(captured.wait(2))
                    app.command('pause', {})
                    release.set(); time.sleep(0.15)
                    handle.assert_not_called()
            finally:
                release.set(); app.close()

    def test_real_http_queue_singleflight_cache_and_auth(self):
        class Engine:
            revision = "test-v1"
            calls = 0
            def generate(self, request):
                self.calls += 1
                time.sleep(0.05)
                return b"test-audio", "audio/ogg"
        with tempfile.TemporaryDirectory() as directory:
            engine = Engine()
            service = VoiceService(Path(directory) / "server", Catalog({"schema_version": 1, "items": []}), engine)
            server = make_server(service, port=0, token="test-token")
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            try:
                cache = AudioCache(Path(directory) / "client", 100)
                provider = RemoteProvider(cache, f"http://127.0.0.1:{server.server_port}", "test-token")
                request = canonical_request({"action": "demo"})
                one = service.submit(request); two = service.submit(request)
                self.assertEqual(one["id"], two["id"])
                key, path = provider.get_audio(request, threading.Event(), lambda message: None)
                self.assertEqual(path.read_bytes(), b"test-audio")
                self.assertEqual(engine.calls, 1)
                self.assertEqual(provider.get_audio(request, threading.Event(), lambda message: None)[0], key)
                self.assertEqual(engine.calls, 1)
                import urllib.error
                with self.assertRaises(urllib.error.HTTPError) as error:
                    RemoteProvider(cache, f"http://127.0.0.1:{server.server_port}", "wrong").get_audio(request, threading.Event(), lambda message: None)
                self.assertEqual(error.exception.code, 401)
            finally:
                server.shutdown(); server.server_close(); service.close(); thread.join()

    def test_late_download_cannot_play_after_stop(self):
        class Provider:
            def get_audio(self, request, cancelled, status):
                time.sleep(0.1)
                return "a" * 64, Path("unused")
        class Player:
            calls = 0
            def stop(self): pass
            def play(self, path, done): self.calls += 1
        with tempfile.TemporaryDirectory() as directory:
            player = Player(); cache = AudioCache(Path(directory))
            controller = Controller(cache, Provider(), player)
            controller.handle({"action": "demo"}); controller.handle({"action": "stop"})
            time.sleep(0.15)
            self.assertEqual(player.calls, 0)
            controller.close()


if __name__ == "__main__":
    unittest.main()
