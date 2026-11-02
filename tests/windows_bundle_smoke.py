"""Launch the shipped exe/runtime from a Unicode path, without system Python."""
import ctypes as c
from ctypes import wintypes as w
import os
from pathlib import Path
import subprocess
import tempfile
import time
from zipfile import ZipFile

user=c.WinDLL('user32',use_last_error=True)
user.FindWindowW.argtypes,user.FindWindowW.restype=[w.LPCWSTR,w.LPCWSTR],w.HWND
user.PostMessageW.argtypes=[w.HWND,w.UINT,w.WPARAM,w.LPARAM]
with tempfile.TemporaryDirectory() as directory:
    root=Path(directory)/'Проверка Киро с пробелами'
    with ZipFile('downloads/WarcraftAudi-0.6.0-Windows.zip') as archive:archive.extractall(root)
    bundle=root/'WarcraftAudiCompanion'
    env={**os.environ,'LOCALAPPDATA':str(root/'data')}
    assert not user.FindWindowW('WarcraftAudiDesktop',None)
    launcher=subprocess.Popen([str(bundle/'WarcraftAudi.exe')],cwd=directory,env=env)
    assert launcher.wait(timeout=10)==0
    hwnd=None;deadline=time.monotonic()+30
    while not hwnd and time.monotonic()<deadline:
        hwnd=user.FindWindowW('WarcraftAudiDesktop',None);time.sleep(.1)
    if not hwnd:
        log=root/'data/WarcraftAudi/app.log'
        raise AssertionError(log.read_text(encoding='utf-8') if log.exists() else 'Packaged application did not open')
    # A second launch must bring the original native window forward.
    assert subprocess.run([str(bundle/'WarcraftAudi.exe')],cwd=directory,env=env,timeout=10).returncode==0
    time.sleep(1)
    assert user.FindWindowW('WarcraftAudiDesktop',None)==hwnd
    user.PostMessageW(hwnd,0x10,0,0)
    deadline=time.monotonic()+10
    while user.FindWindowW('WarcraftAudiDesktop',None) and time.monotonic()<deadline:time.sleep(.1)
    assert not user.FindWindowW('WarcraftAudiDesktop',None)
    # Let pythonw finish releasing its DLLs before Windows removes the temp folder.
    time.sleep(1)
    print('BUNDLE_SMOKE_OK: shipped exe, embedded Python, native window, Unicode/space path, duplicate launch and shutdown',flush=True)
