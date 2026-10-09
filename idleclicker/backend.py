"""The operating-system operations the bot needs, behind one small interface.

The real implementation lives in ``win32_backend`` and talks to Windows through
ctypes (no admin rights, no hooks, no injection). Tests use a fake.

All coordinates are physical screen pixels.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from typing import List, Optional, Tuple

try:  # Python 3.8+
    from typing import Protocol
except ImportError:  # pragma: no cover
    Protocol = object  # type: ignore[assignment,misc]

log = logging.getLogger(__name__)

Point = Tuple[int, int]
RGB = Tuple[int, int, int]


@dataclass(frozen=True)
class Rect:
    left: int
    top: int
    width: int
    height: int

    def contains(self, x: int, y: int) -> bool:
        return (self.left <= x < self.left + self.width
                and self.top <= y < self.top + self.height)


@dataclass(frozen=True)
class Image:
    """A screen capture: rows top to bottom, 4 bytes per pixel in B, G, R, unused order."""
    width: int
    height: int
    bgra: bytes

    def pixel(self, x: int, y: int) -> RGB:
        i = 4 * (y * self.width + x)
        return self.bgra[i + 2], self.bgra[i + 1], self.bgra[i]


@dataclass(frozen=True)
class WindowInfo:
    handle: int
    title: str
    class_name: str


# Names accepted for the pause/quit hotkeys, mapped to Windows virtual-key codes.
KEY_CODES = {f"F{n}": 0x6F + n for n in range(1, 25)}
KEY_CODES.update({
    "CTRL": 0x11, "ESC": 0x1B, "PAUSE": 0x13, "SCROLLLOCK": 0x91, "INSERT": 0x2D,
    "DELETE": 0x2E, "HOME": 0x24, "END": 0x23, "PAGEUP": 0x21, "PAGEDOWN": 0x22,
})
KEY_CODES.update({c: ord(c) for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"})


class Backend(Protocol):
    def list_windows(self) -> List[WindowInfo]:
        """Visible, titled top-level windows, excluding this program's own."""

    def window_info(self, handle: int) -> Optional[WindowInfo]: ...

    def is_window(self, handle: int) -> bool: ...

    def is_minimized(self, handle: int) -> bool: ...

    def foreground_window(self) -> Optional[int]: ...

    def client_rect(self, handle: int) -> Optional[Rect]:
        """The window's client area (no title bar/borders) in screen coordinates."""

    def window_at(self, x: int, y: int) -> Optional[int]:
        """The top-level window that would receive a click at this screen point."""

    def cursor_pos(self) -> Point: ...

    def is_mouse_down(self) -> bool:
        """Whether the primary mouse button is currently held."""

    def is_key_down(self, key: str) -> bool:
        """Whether a key from KEY_CODES is currently held."""

    def click(self, x: int, y: int, hold_seconds: float) -> bool:
        """Move to (x, y) and click the primary button. False if Windows refused."""

    def move_cursor(self, x: int, y: int) -> None:
        """Move the mouse pointer without clicking."""

    def grab(self, left: int, top: int, width: int, height: int) -> Image:
        """Copy a rectangle of the screen."""

    def sample_color(self, x: int, y: int, radius: int) -> RGB:
        """Average colour of the (2*radius+1)^2 pixel square centred on (x, y)."""


class UnsupportedPlatformError(RuntimeError):
    pass


def create_backend() -> Backend:
    if sys.platform != "win32":
        raise UnsupportedPlatformError(
            "This auto-clicker only runs on Windows (where the Steam game runs). "
            f"Detected platform: {sys.platform}.")
    from .win32_backend import Win32Backend
    return Win32Backend()


def find_window(backend: Backend, title: str, class_name: str = "",
                match: str = "contains") -> Optional[WindowInfo]:
    """Find the game window by title (case-insensitive).

    An exact title match always wins over a partial one. ``class_name``, when
    set, must match exactly; it stops e.g. a browser tab titled after the game
    from being picked.
    """
    wanted = title.casefold()
    exact, partial = [], []
    for window in backend.list_windows():
        if class_name and window.class_name != class_name:
            continue
        current = window.title.casefold()
        if current == wanted:
            exact.append(window)
        elif match == "contains" and wanted in current:
            partial.append(window)
    candidates = exact or partial
    if len(candidates) > 1:
        log.warning("Several windows match %r: %s. Using %r. Set a more specific "
                    "window_title in config.json if that's wrong.", title,
                    ", ".join(repr(w.title) for w in candidates), candidates[0].title)
    return candidates[0] if candidates else None
