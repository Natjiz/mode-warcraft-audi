"""Small native Windows UI. No browser, local HTTP server or WebView runtime."""
from __future__ import annotations

import ctypes as c
from ctypes import wintypes as w
import logging
import os

CLASS_NAME = 'WarcraftAudiDesktop'


def enable_dpi_awareness():
    try:
        c.WinDLL('user32').SetProcessDpiAwarenessContext(c.c_void_p(-4))
    except (AttributeError, OSError):
        pass


def show_existing():
    user = c.WinDLL('user32', use_last_error=True)
    user.FindWindowW.argtypes, user.FindWindowW.restype = [w.LPCWSTR, w.LPCWSTR], w.HWND
    user.ShowWindow.argtypes = [w.HWND, c.c_int]
    user.SetForegroundWindow.argtypes = [w.HWND]
    hwnd = user.FindWindowW(CLASS_NAME, None)
    if hwnd:
        user.ShowWindow(hwnd, 9)  # Restore a minimized window.
        user.SetForegroundWindow(hwnd)


class NativeWindow:
    def __init__(self, app):
        if os.name != 'nt':
            raise RuntimeError('Окно программы предназначено для Windows 10/11')
        self.app, self.expanded, self.controls, self.advanced = app, False, {}, []
        self.user, self.gdi, self.kernel = (c.WinDLL(n, use_last_error=True) for n in ('user32', 'gdi32', 'kernel32'))
        self.callback_type = c.WINFUNCTYPE(c.c_ssize_t, w.HWND, w.UINT, w.WPARAM, w.LPARAM)
        class WNDCLASS(c.Structure):
            _fields_ = [('style', w.UINT), ('lpfnWndProc', self.callback_type), ('cbClsExtra', c.c_int),
                        ('cbWndExtra', c.c_int), ('hInstance', w.HINSTANCE), ('hIcon', w.HICON),
                        ('hCursor', w.HANDLE), ('hbrBackground', w.HBRUSH), ('lpszMenuName', w.LPCWSTR), ('lpszClassName', w.LPCWSTR)]
        self.WNDCLASS = WNDCLASS
        prototypes = {
            'CreateWindowExW': ([w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD, c.c_int, c.c_int, c.c_int, c.c_int, w.HWND, w.HMENU, w.HINSTANCE, w.LPVOID], w.HWND),
            'DefWindowProcW': ([w.HWND, w.UINT, w.WPARAM, w.LPARAM], c.c_ssize_t),
            'RegisterClassW': ([c.POINTER(WNDCLASS)], w.ATOM),
            'SendMessageW': ([w.HWND, w.UINT, w.WPARAM, w.LPARAM], c.c_ssize_t),
            'SetWindowTextW': ([w.HWND, w.LPCWSTR], w.BOOL),
            'GetWindowTextW': ([w.HWND, w.LPWSTR, c.c_int], c.c_int),
            'GetWindowTextLengthW': ([w.HWND], c.c_int),
            'ShowWindow': ([w.HWND, c.c_int], w.BOOL),
            'SetWindowPos': ([w.HWND, w.HWND, c.c_int, c.c_int, c.c_int, c.c_int, w.UINT], w.BOOL),
            'GetMessageW': ([c.POINTER(w.MSG), w.HWND, w.UINT, w.UINT], w.BOOL),
            'TranslateMessage': ([c.POINTER(w.MSG)], w.BOOL),
            'DispatchMessageW': ([c.POINTER(w.MSG)], c.c_ssize_t),
            'SetTimer': ([w.HWND, c.c_size_t, w.UINT, w.LPVOID], c.c_size_t),
            'KillTimer': ([w.HWND, c.c_size_t], w.BOOL),
            'DestroyWindow': ([w.HWND], w.BOOL),
            'PostQuitMessage': ([c.c_int], None),
            'MessageBoxW': ([w.HWND, w.LPCWSTR, w.LPCWSTR, w.UINT], c.c_int),
            'LoadCursorW': ([w.HINSTANCE, w.LPCWSTR], w.HANDLE),
        }
        for name, (args, result) in prototypes.items():
            function = getattr(self.user, name); function.argtypes, function.restype = args, result
        self.kernel.GetModuleHandleW.argtypes, self.kernel.GetModuleHandleW.restype = [w.LPCWSTR], w.HMODULE
        self.gdi.GetStockObject.argtypes, self.gdi.GetStockObject.restype = [c.c_int], w.HANDLE
        self.instance = self.kernel.GetModuleHandleW(None)
        self.callback = self.callback_type(self.window_proc)  # Keep callback alive for the message loop.
        wc = WNDCLASS()
        wc.lpfnWndProc, wc.hInstance, wc.lpszClassName = self.callback, self.instance, CLASS_NAME
        wc.hCursor = self.user.LoadCursorW(None, c.cast(c.c_void_p(32512), w.LPCWSTR))
        wc.hbrBackground = c.c_void_p(6)  # COLOR_WINDOW + 1
        if not self.user.RegisterClassW(c.byref(wc)):
            raise c.WinError(c.get_last_error())
        self.hwnd = self.user.CreateWindowExW(0, CLASS_NAME, 'Warcraft Audi', 0x00CA0000,
            -2147483648, -2147483648, 540, 330, None, None, self.instance, None)
        if not self.hwnd:
            raise c.WinError(c.get_last_error())
        self._build()
        self.refresh()
        self.user.SetTimer(self.hwnd, 1, 250, None)

    def add(self, key, kind, text, x, y, width, height, identifier=0, style=0, advanced=False):
        hwnd = self.user.CreateWindowExW(0x200 if kind == 'EDIT' else 0, kind, text,
            0x40000000 | (0 if advanced else 0x10000000) | style,
            x, y, width, height, self.hwnd, c.c_void_p(identifier), self.instance, None)
        if not hwnd:
            raise c.WinError(c.get_last_error())
        self.user.SendMessageW(hwnd, 0x30, self.gdi.GetStockObject(17), 1)  # DEFAULT_GUI_FONT
        self.controls[key] = hwnd
        if advanced:
            self.advanced.append(hwnd)
        return hwnd

    def _build(self):
        self.add('heading', 'STATIC', 'Warcraft Audi · озвучка квестов', 22, 18, 490, 24)
        self.add('connection', 'STATIC', '', 22, 55, 490, 22)
        self.add('message', 'STATIC', '', 22, 84, 490, 48)
        self.add('quest', 'STATIC', '', 22, 135, 490, 22)
        self.add('hint', 'STATIC', 'Сверните это окно, войдите в игру и откройте квест.', 22, 169, 490, 22)
        self.add('pause', 'BUTTON', 'Пауза', 22, 210, 140, 30, 101, 0x10000)
        self.add('stop', 'BUTTON', 'Стоп озвучки', 175, 210, 150, 30, 102, 0x10000)
        self.add('settings', 'BUTTON', 'Настройки', 338, 210, 155, 30, 103, 0x10000)
        controls = [
            ('volume_label', 'STATIC', 'Громкость, %', 22, 270, 200, 22, 0, 0),
            ('volume', 'EDIT', str(self.app.config['volume']), 232, 265, 90, 26, 0, 0x10000 | 0x2000),
            ('cache_label', 'STATIC', 'Лимит кэша, МБ', 22, 310, 200, 22, 0, 0),
            ('cache', 'EDIT', str(self.app.config['cache_mb']), 232, 305, 90, 26, 0, 0x10000 | 0x2000),
            ('url_label', 'STATIC', 'Сервер новых озвучек (необязательно)', 22, 350, 490, 22, 0, 0),
            ('url', 'EDIT', self.app.config['server_url'], 22, 377, 470, 26, 0, 0x10000 | 0x80),
            ('token_label', 'STATIC', 'Ключ сервера (только до закрытия программы)', 22, 415, 470, 22, 0, 0),
            ('token', 'EDIT', '', 22, 442, 470, 26, 0, 0x10000 | 0x80 | 0x20),
            ('save', 'BUTTON', 'Сохранить', 22, 485, 140, 30, 104, 0x10000),
            ('clear', 'BUTTON', 'Очистить кэш', 175, 485, 150, 30, 105, 0x10000),
            ('demo', 'BUTTON', 'Проба Акунды', 338, 485, 155, 30, 106, 0x10000),
            ('cache_info', 'STATIC', '', 22, 532, 490, 22, 0, 0),
        ]
        for args in controls:
            self.add(*args, advanced=True)

    def text(self, key):
        hwnd = self.controls[key]
        result = c.create_unicode_buffer(self.user.GetWindowTextLengthW(hwnd) + 1)
        self.user.GetWindowTextW(hwnd, result, len(result))
        return result.value

    def refresh(self):
        state = self.app.state()
        connection = {'waiting': 'Ожидаю игру и диалог квеста', 'connected': 'Игра подключена',
                      'paused': 'Озвучка на паузе', 'capture_error': 'Экран игры недоступен'}[state['connection']]
        for key, text in [('connection', connection), ('message', state['message']), ('quest', state['quest']),
                          ('pause', 'Пауза' if state['running'] else 'Продолжить'),
                          ('cache_info', f"Кэш: {state['cache']['bytes'] / 1024**2:.1f} МБ")]:
            self.user.SetWindowTextW(self.controls[key], text)

    def click(self, identifier):
        if identifier == 101:
            self.app.command('pause' if self.app.watch.running.is_set() else 'start')
        elif identifier == 102:
            self.app.command('stop')
        elif identifier == 103:
            self.expanded = not self.expanded
            for hwnd in self.advanced:
                self.user.ShowWindow(hwnd, 5 if self.expanded else 0)
            self.user.SetWindowPos(self.hwnd, None, 0, 0, 540, 610 if self.expanded else 330, 0x6)
        elif identifier == 104:
            volume = int(self.text('volume'))
            if not 0 <= volume <= 100:
                raise ValueError('Громкость: 0–100')
            self.app.command('settings', {'cache_mb': self.text('cache'), 'server_url': self.text('url'), 'server_token': self.text('token')})
            self.app.command('volume', {'volume': volume})
            self.user.SetWindowTextW(self.controls['token'], '')
        elif identifier == 105:
            self.app.command('clear')
        elif identifier == 106:
            self.app.command('demo')
        self.refresh()

    def window_proc(self, hwnd, message, wp, lp):
        try:
            if message == 0x111 and (wp >> 16) == 0:  # WM_COMMAND / BN_CLICKED
                self.click(wp & 0xffff)
                return 0
            if message == 0x113 and self.controls:  # WM_TIMER
                self.refresh()
                return 0
            if message == 0x10:  # WM_CLOSE
                self.user.DestroyWindow(hwnd)
                return 0
            if message == 2:  # WM_DESTROY
                self.user.KillTimer(hwnd, 1)
                self.user.PostQuitMessage(0)
                return 0
        except (ValueError, OSError) as error:
            self.user.MessageBoxW(hwnd, str(error), 'Warcraft Audi', 0x30)
            return 0
        except Exception:
            logging.exception('Native window callback failed')
            return 0
        return self.user.DefWindowProcW(hwnd, message, wp, lp)

    def run(self):
        self.user.ShowWindow(self.hwnd, 1)
        message = w.MSG()
        while True:
            result = self.user.GetMessageW(c.byref(message), None, 0, 0)
            if result == 0:
                break
            if result == -1:
                raise c.WinError(c.get_last_error())
            self.user.TranslateMessage(c.byref(message))
            self.user.DispatchMessageW(c.byref(message))
