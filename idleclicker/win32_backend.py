"""Windows implementation of the backend, using only ctypes and documented,
unprivileged user32/gdi32 calls: no admin rights, no hooks, no DLL injection,
no reading of the game's memory.

Clicks are ordinary synthesized mouse input (SetCursorPos + SendInput), the
same thing any macro tool does. Colours are read by copying a few pixels off
the screen (BitBlt), exactly like taking a tiny screenshot.
"""

from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes
from typing import List, Optional

from .backend import KEY_CODES, RGB, Point, Rect, WindowInfo

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

ULONG_PTR = ctypes.c_size_t

INPUT_MOUSE = 0
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
VK_LBUTTON = 0x01
VK_RBUTTON = 0x02
SM_SWAPBUTTON = 23
GA_ROOT = 2
SRCCOPY = 0x00CC0020
BI_RGB = 0
DIB_RGB_COLORS = 0


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    # All three members are needed so sizeof(INPUT) matches what SendInput expects.
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def _sig(func, restype, *argtypes):
    func.restype = restype
    func.argtypes = argtypes


# Explicit signatures: without them ctypes truncates 64-bit handles to int.
_sig(user32.EnumWindows, wintypes.BOOL, WNDENUMPROC, wintypes.LPARAM)
_sig(user32.IsWindow, wintypes.BOOL, wintypes.HWND)
_sig(user32.IsWindowVisible, wintypes.BOOL, wintypes.HWND)
_sig(user32.IsIconic, wintypes.BOOL, wintypes.HWND)
_sig(user32.GetWindowTextLengthW, ctypes.c_int, wintypes.HWND)
_sig(user32.GetWindowTextW, ctypes.c_int, wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
_sig(user32.GetClassNameW, ctypes.c_int, wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
_sig(user32.GetWindowThreadProcessId, wintypes.DWORD, wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
_sig(user32.GetForegroundWindow, wintypes.HWND)
_sig(user32.GetClientRect, wintypes.BOOL, wintypes.HWND, ctypes.POINTER(wintypes.RECT))
_sig(user32.ClientToScreen, wintypes.BOOL, wintypes.HWND, ctypes.POINTER(wintypes.POINT))
_sig(user32.WindowFromPoint, wintypes.HWND, wintypes.POINT)
_sig(user32.GetAncestor, wintypes.HWND, wintypes.HWND, wintypes.UINT)
_sig(user32.GetCursorPos, wintypes.BOOL, ctypes.POINTER(wintypes.POINT))
_sig(user32.SetCursorPos, wintypes.BOOL, ctypes.c_int, ctypes.c_int)
_sig(user32.SendInput, wintypes.UINT, wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
_sig(user32.GetAsyncKeyState, wintypes.SHORT, ctypes.c_int)
_sig(user32.GetSystemMetrics, ctypes.c_int, ctypes.c_int)
_sig(user32.GetDC, wintypes.HDC, wintypes.HWND)
_sig(user32.ReleaseDC, ctypes.c_int, wintypes.HWND, wintypes.HDC)
_sig(gdi32.CreateCompatibleDC, wintypes.HDC, wintypes.HDC)
_sig(gdi32.CreateCompatibleBitmap, wintypes.HBITMAP, wintypes.HDC, ctypes.c_int, ctypes.c_int)
_sig(gdi32.SelectObject, wintypes.HGDIOBJ, wintypes.HDC, wintypes.HGDIOBJ)
_sig(gdi32.BitBlt, wintypes.BOOL, wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
     ctypes.c_int, wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD)
_sig(gdi32.GetDIBits, ctypes.c_int, wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
     wintypes.LPVOID, ctypes.POINTER(BITMAPINFO), wintypes.UINT)
_sig(gdi32.DeleteObject, wintypes.BOOL, wintypes.HGDIOBJ)
_sig(gdi32.DeleteDC, wintypes.BOOL, wintypes.HDC)
_sig(kernel32.GetConsoleWindow, wintypes.HWND)


def _enable_dpi_awareness() -> None:
    """Work in physical pixels so coordinates are right on scaled (125%/150%) displays."""
    try:  # Windows 10 1703+: per-monitor v2
        func = user32.SetProcessDpiAwarenessContext
        _sig(func, wintypes.BOOL, wintypes.HANDLE)
        if func(wintypes.HANDLE(-4)):
            return
    except AttributeError:
        pass
    try:  # Windows 8.1+
        if ctypes.WinDLL("shcore").SetProcessDpiAwareness(2) == 0:
            return
    except (AttributeError, OSError):
        pass
    user32.SetProcessDPIAware()  # Vista+: system-aware


class Win32Backend:
    def __init__(self) -> None:
        _enable_dpi_awareness()
        self._console = kernel32.GetConsoleWindow() or 0
        self._pid = os.getpid()

    # ------------------------------------------------------------ windows

    def _title(self, hwnd) -> str:
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value

    def _class_name(self, hwnd) -> str:
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buf, 256)
        return buf.value

    def _is_ours(self, hwnd) -> bool:
        if hwnd == self._console:
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value == self._pid

    def list_windows(self) -> List[WindowInfo]:
        handles = []

        @WNDENUMPROC
        def collect(hwnd, _lparam):
            handles.append(hwnd)
            return True

        user32.EnumWindows(collect, 0)
        result = []
        for hwnd in handles:
            if not hwnd or not user32.IsWindowVisible(hwnd) or self._is_ours(hwnd):
                continue
            title = self._title(hwnd)
            if title:
                result.append(WindowInfo(int(hwnd), title, self._class_name(hwnd)))
        return result

    def window_info(self, handle: int) -> Optional[WindowInfo]:
        if not handle or not user32.IsWindow(handle) or self._is_ours(handle):
            return None
        return WindowInfo(int(handle), self._title(handle), self._class_name(handle))

    def is_window(self, handle: int) -> bool:
        return bool(user32.IsWindow(handle))

    def is_minimized(self, handle: int) -> bool:
        return bool(user32.IsIconic(handle))

    def foreground_window(self) -> Optional[int]:
        return user32.GetForegroundWindow() or None

    def client_rect(self, handle: int) -> Optional[Rect]:
        rect = wintypes.RECT()
        origin = wintypes.POINT(0, 0)
        if not user32.GetClientRect(handle, ctypes.byref(rect)):
            return None
        if not user32.ClientToScreen(handle, ctypes.byref(origin)):
            return None
        return Rect(origin.x, origin.y, rect.right - rect.left, rect.bottom - rect.top)

    def window_at(self, x: int, y: int) -> Optional[int]:
        hwnd = user32.WindowFromPoint(wintypes.POINT(x, y))
        if not hwnd:
            return None
        return user32.GetAncestor(hwnd, GA_ROOT) or None

    # ------------------------------------------------------------ input

    def _buttons_swapped(self) -> bool:
        return bool(user32.GetSystemMetrics(SM_SWAPBUTTON))

    def cursor_pos(self) -> Point:
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        return pt.x, pt.y

    def is_mouse_down(self) -> bool:
        # GetAsyncKeyState reports physical buttons, so follow the swap setting.
        vk = VK_RBUTTON if self._buttons_swapped() else VK_LBUTTON
        return bool(user32.GetAsyncKeyState(vk) & 0x8000)

    def is_key_down(self, key: str) -> bool:
        return bool(user32.GetAsyncKeyState(KEY_CODES[key.upper()]) & 0x8000)

    def _send_mouse(self, flags: int) -> bool:
        event = INPUT(type=INPUT_MOUSE)
        event.u.mi = MOUSEINPUT(0, 0, 0, flags, 0, 0)
        return user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(INPUT)) == 1

    def click(self, x: int, y: int, hold_seconds: float) -> bool:
        if self._buttons_swapped():
            down, up = MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP
        else:
            down, up = MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP
        if not user32.SetCursorPos(x, y):
            return False
        if not self._send_mouse(down):
            return False
        if hold_seconds > 0:
            time.sleep(hold_seconds)
        # Always release, even if something changed meanwhile, so the button never sticks.
        return self._send_mouse(up)

    # ------------------------------------------------------------ pixels

    def sample_color(self, x: int, y: int, radius: int) -> RGB:
        size = 2 * radius + 1
        buf = (ctypes.c_ubyte * (size * size * 4))()
        screen_dc = user32.GetDC(None)
        if not screen_dc:
            raise OSError("GetDC failed")
        try:
            mem_dc = gdi32.CreateCompatibleDC(screen_dc)
            try:
                bitmap = gdi32.CreateCompatibleBitmap(screen_dc, size, size)
                try:
                    previous = gdi32.SelectObject(mem_dc, bitmap)
                    copied = gdi32.BitBlt(mem_dc, 0, 0, size, size, screen_dc,
                                          x - radius, y - radius, SRCCOPY)
                    gdi32.SelectObject(mem_dc, previous)  # GetDIBits needs it deselected
                    if not copied:
                        raise OSError("BitBlt failed")
                    info = BITMAPINFO()
                    header = info.bmiHeader
                    header.biSize = ctypes.sizeof(BITMAPINFOHEADER)
                    header.biWidth = size
                    header.biHeight = -size  # top-down rows
                    header.biPlanes = 1
                    header.biBitCount = 32
                    header.biCompression = BI_RGB
                    if gdi32.GetDIBits(mem_dc, bitmap, 0, size, buf, ctypes.byref(info),
                                       DIB_RGB_COLORS) != size:
                        raise OSError("GetDIBits failed")
                finally:
                    gdi32.DeleteObject(bitmap)
            finally:
                gdi32.DeleteDC(mem_dc)
        finally:
            user32.ReleaseDC(None, screen_dc)
        n = size * size
        # Pixels are stored as B, G, R, unused.
        return (round(sum(buf[2::4]) / n), round(sum(buf[1::4]) / n), round(sum(buf[0::4]) / n))
