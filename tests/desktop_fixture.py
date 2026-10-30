"""Synthetic display + real HTTP/UI/decoder fixture for headless browser checks.

It deliberately does not claim to capture a real Windows game or output sound.
Run from repository root: python -m tests.desktop_fixture (or this file with PYTHONPATH).
"""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
import mss
from companion.warcraft_audi.app import DesktopApp, make_ui
from companion.warcraft_audi.protocol import frame_packet, render_frame


class DisplayFixture:
    monitors = [{"left": 0, "top": 0, "width": 1600, "height": 900}]
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def grab(self, region):
        image = Image.new("RGB", (1600, 900), (28, 39, 48))
        message = json.dumps({"v": 1, "action": "demo"}).encode()
        image.paste(render_frame(frame_packet(message, 1, 1, 0)), (620, 90))
        crop = image.crop((region['left'], region['top'], region['left'] + region['width'], region['top'] + region['height']))
        return SimpleNamespace(rgb=crop.tobytes(), size=crop.size, width=crop.width, height=crop.height)


class DecoderPlayer:
    volume = 0.8
    def stop(self): pass
    def play(self, path, done):
        import miniaudio
        decoded = miniaudio.decode_file(str(path))
        assert len(decoded.samples) > 100


if __name__ == "__main__":
    mss.mss = DisplayFixture
    with tempfile.TemporaryDirectory() as directory:
        app = DesktopApp(Path(directory), DecoderPlayer())
        server = make_ui(app, 18766)
        print("Synthetic browser fixture listening", flush=True)
        try:
            server.serve_forever()
        finally:
            app.close()
            server.server_close()
