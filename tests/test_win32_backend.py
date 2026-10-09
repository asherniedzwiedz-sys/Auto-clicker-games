"""Runs the Windows backend against fake DLLs, on any OS.

Each fake DLL only provides the functions the real one exports (checked against
Wine's export tables), so calling a function on the wrong DLL fails here the
same way it fails on Windows: AttributeError "function '...' not found".
"""

import ctypes
import importlib
import os
import sys
import unittest
from unittest import mock

import idleclicker
from idleclicker.backend import Rect, WindowInfo

EXPORTS = {
    "user32": {
        "ClientToScreen", "EnumWindows", "GetAncestor", "GetAsyncKeyState", "GetClassNameW",
        "GetClientRect", "GetCursorPos", "GetDC", "GetForegroundWindow", "GetSystemMetrics",
        "GetWindowTextLengthW", "GetWindowTextW", "GetWindowThreadProcessId", "IsIconic",
        "IsWindow", "IsWindowVisible", "ReleaseDC", "SendInput", "SetCursorPos",
        "SetProcessDPIAware", "SetProcessDpiAwarenessContext", "WindowFromPoint",
    },
    "gdi32": {
        "BitBlt", "CreateCompatibleBitmap", "CreateCompatibleDC", "DeleteDC", "DeleteObject",
        "GetDIBits", "SelectObject",
    },
    "kernel32": {"GetConsoleWindow"},
    "shcore": {"SetProcessDpiAwareness"},
}

GAME, NOTEPAD, HIDDEN, OWN_WINDOW, CONSOLE, GAME_CHILD = 1, 2, 3, 4, 999, 77
TITLES = {GAME: "Example Clicker", NOTEPAD: "Notepad", HIDDEN: "Hidden",
          OWN_WINDOW: "Python", CONSOLE: "C:\\Windows\\py.exe"}
CLASSES = {GAME: "UnityWndClass", NOTEPAD: "Notepad"}


class FakeFunction:
    def __init__(self, name, impl, calls):
        self.name, self.impl, self.calls = name, impl, calls
        self.restype = self.argtypes = None

    def __call__(self, *args):
        self.calls.append((self.name, args))
        return self.impl(*args) if self.impl else 1


class FakeDLL:
    def __init__(self, name, impls, calls):
        self._name, self._impls, self._calls, self._funcs = name, impls, calls, {}

    def __getattr__(self, attr):
        if attr.startswith("_"):
            raise AttributeError(attr)
        if attr not in EXPORTS[self._name]:
            raise AttributeError(f"function '{attr}' not found")
        if attr not in self._funcs:
            self._funcs[attr] = FakeFunction(f"{self._name}.{attr}", self._impls.get(attr), self._calls)
        return self._funcs[attr]


class FakeWindowsAPI:
    def __init__(self):
        self.calls = []
        self.keys_down = set()
        self.buttons_swapped = False
        self.sent_flags = []

    def load(self, name, **_kwargs):
        return FakeDLL(name.lower(), self._impls(), self.calls)

    def _impls(self):
        def enum_windows(callback, _lparam):
            for hwnd in (GAME, NOTEPAD, HIDDEN, OWN_WINDOW, CONSOLE):
                callback(hwnd, 0)
            return 1

        def get_text(hwnd, buf, size):
            buf.value = TITLES.get(hwnd, "")[:size - 1]
            return len(buf.value)

        def get_class(hwnd, buf, _size):
            buf.value = CLASSES.get(hwnd, "")
            return len(buf.value)

        def thread_pid(hwnd, pid):
            pid._obj.value = os.getpid() if hwnd == OWN_WINDOW else 4242
            return 1

        def client_rect(_hwnd, rect):
            rect._obj.left, rect._obj.top, rect._obj.right, rect._obj.bottom = 0, 0, 800, 600
            return 1

        def client_to_screen(_hwnd, point):
            point._obj.x += 100
            point._obj.y += 50
            return 1

        def cursor_pos(point):
            point._obj.x, point._obj.y = 5, 6
            return 1

        def send_input(count, event, size):
            assert size == ctypes.sizeof(self.module.INPUT)
            self.sent_flags.append(event._obj.u.mi.dwFlags)
            return count

        def get_dibits(_dc, _bitmap, _start, lines, buf, info, _usage):
            assert info._obj.bmiHeader.biHeight == -lines  # top-down rows
            for i in range(len(buf) // 4):  # alternate two colours, stored B, G, R, x
                buf[4 * i:4 * i + 4] = [10, 20, 30, 0] if i % 2 == 0 else [50, 60, 70, 0]
            return lines

        return {
            "GetConsoleWindow": lambda: CONSOLE,
            "EnumWindows": enum_windows,
            "IsWindowVisible": lambda hwnd: hwnd != HIDDEN,
            "IsWindow": lambda hwnd: hwnd in TITLES,
            "IsIconic": lambda hwnd: 0,
            "GetWindowTextLengthW": lambda hwnd: len(TITLES.get(hwnd, "")),
            "GetWindowTextW": get_text,
            "GetClassNameW": get_class,
            "GetWindowThreadProcessId": thread_pid,
            "GetForegroundWindow": lambda: GAME,
            "GetClientRect": client_rect,
            "ClientToScreen": client_to_screen,
            "WindowFromPoint": lambda point: GAME_CHILD,
            "GetAncestor": lambda hwnd, flag: GAME if (hwnd, flag) == (GAME_CHILD, 2) else None,
            "GetCursorPos": cursor_pos,
            "GetAsyncKeyState": lambda vk: -32768 if vk in self.keys_down else 0,
            "GetSystemMetrics": lambda index: int(self.buttons_swapped) if index == 23 else 0,
            "SendInput": send_input,
            "GetDIBits": get_dibits,
        }


class Win32BackendTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeWindowsAPI()
        for target, value in (("WinDLL", self.api.load), ("WINFUNCTYPE", ctypes.CFUNCTYPE)):
            patcher = mock.patch.object(ctypes, target, value, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)
        sys.modules.pop("idleclicker.win32_backend", None)
        self.addCleanup(sys.modules.pop, "idleclicker.win32_backend", None)
        self.addCleanup(vars(idleclicker).pop, "win32_backend", None)
        self.api.module = importlib.import_module("idleclicker.win32_backend")
        self.backend = self.api.module.Win32Backend()

    def called(self, name):
        return [args for fn, args in self.api.calls if fn == name]

    def test_startup_turns_on_dpi_awareness_and_finds_own_console(self):
        self.assertTrue(self.called("user32.SetProcessDpiAwarenessContext"))
        self.assertTrue(self.called("kernel32.GetConsoleWindow"))

    def test_list_windows_skips_hidden_and_own_windows(self):
        self.assertEqual(self.backend.list_windows(), [
            WindowInfo(GAME, "Example Clicker", "UnityWndClass"),
            WindowInfo(NOTEPAD, "Notepad", "Notepad"),
        ])

    def test_window_info(self):
        self.assertEqual(self.backend.window_info(GAME).title, "Example Clicker")
        self.assertIsNone(self.backend.window_info(CONSOLE))
        self.assertIsNone(self.backend.window_info(OWN_WINDOW))

    def test_window_state(self):
        self.assertTrue(self.backend.is_window(GAME))
        self.assertFalse(self.backend.is_minimized(GAME))
        self.assertEqual(self.backend.foreground_window(), GAME)

    def test_client_rect_in_screen_coordinates(self):
        self.assertEqual(self.backend.client_rect(GAME), Rect(100, 50, 800, 600))

    def test_window_at_returns_top_level_window(self):
        self.assertEqual(self.backend.window_at(150, 60), GAME)

    def test_cursor_keys_and_mouse(self):
        self.assertEqual(self.backend.cursor_pos(), (5, 6))
        self.api.keys_down = {0x77}  # F8
        self.assertTrue(self.backend.is_key_down("f8"))
        self.assertFalse(self.backend.is_key_down("F9"))
        self.api.keys_down = {0x01}  # left button
        self.assertTrue(self.backend.is_mouse_down())
        self.api.buttons_swapped = True
        self.assertFalse(self.backend.is_mouse_down())

    def test_click_moves_then_presses_and_releases(self):
        self.assertTrue(self.backend.click(10, 20, 0))
        self.assertEqual(self.called("user32.SetCursorPos"), [(10, 20)])
        self.assertEqual(self.api.sent_flags, [0x0002, 0x0004])  # LEFTDOWN, LEFTUP

    def test_click_uses_primary_button_when_buttons_swapped(self):
        self.api.buttons_swapped = True
        self.backend.click(10, 20, 0)
        self.assertEqual(self.api.sent_flags, [0x0008, 0x0010])  # RIGHTDOWN, RIGHTUP

    def test_move_cursor_does_not_click(self):
        self.backend.move_cursor(30, 40)
        self.assertEqual(self.called("user32.SetCursorPos"), [(30, 40)])
        self.assertEqual(self.api.sent_flags, [])

    def test_grab_copies_the_rectangle(self):
        image = self.backend.grab(200, 300, 4, 2)
        (args,) = self.called("gdi32.BitBlt")
        self.assertEqual(args[1:5] + args[6:8], (0, 0, 4, 2, 200, 300))
        self.assertEqual((image.width, image.height, len(image.bgra)), (4, 2, 32))
        self.assertEqual(image.pixel(0, 0), (30, 20, 10))
        self.assertEqual(image.pixel(1, 0), (70, 60, 50))

    def test_sample_color_averages_pixels_as_rgb(self):
        # 7x7 = 49 pixels: 25 of RGB(30, 20, 10) and 24 of RGB(70, 60, 50).
        expected = tuple(round((25 * a + 24 * b) / 49) for a, b in ((30, 70), (20, 60), (10, 50)))
        self.assertEqual(self.backend.sample_color(100, 100, 3), expected)
        (args,) = self.called("gdi32.BitBlt")
        self.assertEqual(args[1:5], (0, 0, 7, 7))
        self.assertEqual(args[6:8], (97, 97))  # copied from the square centred on (100, 100)
        for cleanup in ("gdi32.DeleteObject", "gdi32.DeleteDC", "user32.ReleaseDC"):
            self.assertTrue(self.called(cleanup), cleanup)


if __name__ == "__main__":
    unittest.main()
