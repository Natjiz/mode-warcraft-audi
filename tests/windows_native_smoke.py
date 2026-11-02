"""Real Win32 controls + Windows mss capture; no actual WoW/hardware audio claim."""
import ctypes as c
from ctypes import wintypes as w
import json
from pathlib import Path
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
from companion.warcraft_audi.app import DesktopApp
from companion.warcraft_audi.native_ui import NativeWindow, enable_dpi_awareness
from companion.warcraft_audi.protocol import frame_packet, render_frame, WIDTH, HEIGHT


def main():
    enable_dpi_awareness()
    errors, played = [], threading.Event()
    class Player:
        volume = .8
        def stop(self): pass
        def play(self, path, done):
            import miniaudio
            assert len(miniaudio.decode_file(str(path)).samples) > 100
            played.set()
    with tempfile.TemporaryDirectory() as directory:
        app = DesktopApp(Path(directory), Player())
        window = NativeWindow(app)
        user, gdi = window.user, window.gdi
        user.PostMessageW.argtypes = [w.HWND,w.UINT,w.WPARAM,w.LPARAM]
        user.LoadImageW.argtypes, user.LoadImageW.restype = [w.HINSTANCE,w.LPCWSTR,w.UINT,c.c_int,c.c_int,w.UINT], w.HANDLE
        gdi.DeleteObject.argtypes = [w.HANDLE]
        fixture = user.CreateWindowExW(0x8,'STATIC','Optical protocol test',0x9000000e,750,50,WIDTH,HEIGHT,None,None,window.instance,None)
        assert fixture
        source = json.loads(Path('quests/kiro_48313_ruRU.json').read_text(encoding='utf-8'))['quests'][0]
        data = json.dumps({**source,'v':1,'action':'play','gender':'male'},ensure_ascii=False).encode()
        packets = [frame_packet(data,27,1,n) for n in range((len(data)+231)//232)]
        def drive():
            try:
                # The program was started first: the signal appears afterwards.
                time.sleep(.4)
                deadline, page = time.monotonic()+35, 0
                while not played.is_set() and time.monotonic()<deadline:
                    path=Path(directory)/'frame.bmp'
                    render_frame(packets[page%len(packets)]).save(path)
                    bitmap=user.LoadImageW(None,str(path),0,0,0,0x10)
                    assert bitmap
                    old=user.SendMessageW(fixture,0x172,0,bitmap)
                    if old:gdi.DeleteObject(old)
                    page+=1;time.sleep(.13)
                assert played.is_set(), app.state()
                assert app.watch.connection=='connected'
                # Controls are real Win32 controls; click through their window messages.
                user.PostMessageW(window.hwnd,0x111,101,0)
                time.sleep(.3)
                assert not app.watch.running.is_set()
                user.PostMessageW(window.hwnd,0x111,103,0)
                time.sleep(.2)
                user.SetWindowTextW(window.controls['cache'],'64')
                user.PostMessageW(window.hwnd,0x111,104,0)
                time.sleep(.3)
                assert app.config['cache_mb']==64
                assert not app.watch.running.is_set(), 'Settings must preserve pause'
                user.PostMessageW(window.hwnd,0x111,101,0)
                time.sleep(.3)
                assert app.watch.running.is_set()
                import mss
                with mss.mss() as capture:
                    shot=capture.grab(capture.monitors[1])
                    Image.frombytes('RGB',shot.size,shot.rgb).save('windows-native.png')
                print('NATIVE_SMOKE_OK: Win32 UI, automatic Windows capture, multi-page Kiro request, public audio SHA verification and native audio decoding',flush=True)
            except BaseException as error:
                errors.append(error)
            finally:
                user.PostMessageW(fixture,0x10,0,0)
                user.PostMessageW(window.hwnd,0x10,0,0)
        driver=threading.Thread(target=drive,daemon=True);driver.start()
        try:window.run()
        finally:app.close();driver.join(timeout=3)
        if errors:raise errors[0]


if __name__=='__main__':main()
