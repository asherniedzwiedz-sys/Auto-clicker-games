"""--calibrate and --calibrate-colors: the interactive set-up walkthrough.

Clicks are detected by polling the mouse button state (GetAsyncKeyState), so no
mouse hook is installed. The clicks still reach the game as normal clicks.
"""

from __future__ import annotations

import dataclasses
import time
from pathlib import Path
from typing import Callable, Optional, Tuple

from .backend import RGB, Backend, Point, WindowInfo, find_window
from .colors import distance
from .config import Business, Config, ConfigError, load_config, save_config, to_screen

HOVER_MARGIN_PX = 30
COLOR_WAIT_SECONDS = 60     # how long to wait for the upgrade button to become visible
COLOR_POLL_SECONDS = 0.1
COLOR_SETTLE_SECONDS = 0.5  # let the game finish redrawing after it comes to the front
# Windows' own taskbar and desktop windows: never the game.
SHELL_WINDOW_CLASSES = {"Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Progman", "WorkerW"}


class CalibrationError(Exception):
    pass


class Calibrator:
    def __init__(self, backend: Backend, *, ask: Callable[[str], str] = input,
                 out: Callable[[str], None] = print, sleep=time.sleep) -> None:
        self.backend = backend
        self.ask = ask
        self.out = out
        self.sleep = sleep

    # ------------------------------------------------------------ prompts

    def yes_no(self, question: str, default: bool) -> bool:
        hint = "[Y/n]" if default else "[y/N]"
        while True:
            answer = self.ask(f"{question} {hint} ").strip().lower()
            if not answer:
                return default
            if answer in ("y", "yes"):
                return True
            if answer in ("n", "no"):
                return False
            self.out("Please answer y or n.")

    def wait_for_click(self) -> Point:
        """Block until the primary mouse button is pressed and released; return where it was pressed."""
        while self.backend.is_mouse_down():  # a button still held from before
            self.sleep(0.01)
        while not self.backend.is_mouse_down():
            self.sleep(0.01)
        pos = self.backend.cursor_pos()
        while self.backend.is_mouse_down():
            self.sleep(0.01)
        return pos

    # ------------------------------------------------------------ full calibration

    def calibrate(self, path: Path) -> Config:
        previous = self._load_previous(path)
        self.out(
            "\n=== Calibration ===\n"
            "Before starting:\n"
            "  * Run the game in WINDOWED or borderless mode at the size you'll play at.\n"
            "  * Your clicks go through to the game as normal: tapping a business is\n"
            "    harmless, and clicking an upgrade button may buy it.\n"
            "Press Ctrl+C at any time to cancel without changing anything.\n")

        window, size = self._pick_window()
        title = self._ask_title(window)
        name = self._ask_name(previous)

        self.out(f"\nStep 3: in the game, click '{name}' and then its upgrade button.\n"
                 "Click the middle of each. You'll hear a beep after each one.")
        tile = self._capture(window.handle, size, f"  1. Click the business TILE for '{name}' (the one to tap)...")
        upgrade = self._capture(window.handle, size, f"  2. Click the UPGRADE button for '{name}'...")
        businesses = [Business(name=name, tile=tile, upgrade=upgrade)]

        if previous is not None:
            config = dataclasses.replace(previous, window_title=title, window_class=window.class_name,
                                         client_size=size, businesses=businesses)
        else:
            config = Config(window_title=title, window_class=window.class_name,
                            client_size=size, businesses=businesses)

        self.out("\nStep 4 (recommended): the upgrade button's colour.")
        self.calibrate_colors(config)

        backup = save_config(config, path)
        self.out(f"\nSaved {path}" + (f" (previous version kept as {backup.name})" if backup else "") + ".")
        self.out("Next: run 'Start Clicker' (or --dry-run -v first to check it).\n"
                 "To switch to a different business later, just run Calibrate again.")
        return config

    def _load_previous(self, path: Path) -> Optional[Config]:
        if not Path(path).exists():
            return None
        try:
            return load_config(path)
        except ConfigError as e:
            self.out(f"(Ignoring the existing {path}: {e})")
            return None

    def _pick_window(self) -> Tuple[WindowInfo, Tuple[int, int]]:
        self.out("Step 1: bring the game to the front, then click anywhere on the game itself\n"
                 "(not on its taskbar button).")
        while True:
            pos = self.wait_for_click()
            handle = self.backend.window_at(*pos)
            info = self.backend.window_info(handle) if handle else None
            if info is None:
                self.out("  That isn't a window I can use (was it this console?). Click inside the game.")
                continue
            if info.class_name in SHELL_WINDOW_CLASSES:
                self.out("  That was the taskbar or the desktop. Bring the game to the front, then\n"
                         "  click on the game itself.")
                continue
            if not info.title.strip():
                self.out("  That window has no title, so the bot couldn't find it again. Click on\n"
                         "  the game itself.")
                continue
            client = self.backend.client_rect(info.handle)
            if client is None or not client.contains(*pos):
                self.out("  That was the title bar or border. Click inside the game itself.")
                continue
            self.out(f"  Found window '{info.title}', {client.width}x{client.height} pixels inside.")
            if self.yes_no("Is that the game?", True):
                return info, (client.width, client.height)
            self.out("OK, click inside the game window.")

    def _ask_title(self, window: WindowInfo) -> str:
        self.out("\nThe bot finds the game by its window title. You can shorten it to the part\n"
                 "that never changes (e.g. drop a version number).")
        while True:
            title = self.ask(f"Title to look for [Enter = '{window.title}']: ").strip()
            if not title:
                return window.title
            if title.casefold() in window.title.casefold():
                return title
            self.out(f"  '{title}' isn't part of '{window.title}'. Try again.")

    def _ask_name(self, previous: Optional[Config]) -> str:
        self.out("\nStep 2: which business should the bot tap and upgrade? Pick your best earner.\n"
                 "The name is just a label for you; it doesn't have to match the game.")
        default = previous.businesses[0].name if previous is not None and previous.businesses else "My business"
        return self.ask(f"Business name [Enter = '{default}']: ").strip() or default

    def _capture(self, handle: int, size: Tuple[int, int], prompt: str) -> Point:
        self.out(prompt)
        while True:
            pos = self.wait_for_click()
            if self.backend.window_at(*pos) != handle:
                self.out("  That click wasn't on the game. Try again.")
                continue
            client = self.backend.client_rect(handle)
            if client is None or not client.contains(*pos):
                self.out("  That was the title bar or border. Click inside the game.")
                continue
            if (client.width, client.height) != size:
                raise CalibrationError("The game window changed size during calibration. Keep it the "
                                       "same size and run --calibrate again.")
            offset = (pos[0] - client.left, pos[1] - client.top)
            self.out(f"  Recorded {offset[0]}, {offset[1]}.\a")
            return offset

    # ------------------------------------------------------------ colours

    def calibrate_colors(self, config: Config) -> None:
        """Sample the upgrade button's colour for each business, in place."""
        window = find_window(self.backend, config.window_title, config.window_class, config.title_match)
        if window is None:
            raise CalibrationError(f"Can't find the game window '{config.window_title}'. "
                                   "Start the game first.")
        self.out("This teaches the bot what the upgrade button looks like when you CAN afford it\n"
                 "(lit up) or CAN'T (greyed out). Without it, the bot just clicks the upgrade\n"
                 "button every few seconds, which is harmless if the game ignores clicks on\n"
                 "buttons you can't afford.")
        for b in config.businesses:
            lit = self._ask_lit(b.name)
            if lit is None:
                continue
            color = self._capture_color(window.handle, config, b.upgrade)
            if color is None:
                continue
            if lit:
                b.upgrade_ready_color = color
            else:
                b.upgrade_disabled_color = color
            self._warn_if_similar(b.name, "upgrade", b.upgrade_ready_color, b.upgrade_disabled_color)
            if b.upgrade_ready_color is None or b.upgrade_disabled_color is None:
                self.out("  Tip: when the button is in its OTHER state (lit up / greyed out),\n"
                         "  double-click 'Calibrate Colours' to add it. With both, the bot is most\n"
                         "  reliable.")

    def _ask_lit(self, name: str) -> Optional[bool]:
        while True:
            answer = self.ask(f"Look at the game: is the UPGRADE button for '{name}' lit up (affordable)\n"
                              "right now? [y/n, Enter = skip] ").strip().lower()
            if answer in ("", "s", "skip"):
                return None
            if answer in ("y", "yes", "n", "no"):
                return answer.startswith("y")
            self.out("  Please answer y or n, or press Enter to skip.")

    def _capture_color(self, handle: int, config: Config, offset: Point) -> Optional[RGB]:
        """Read the button's colour as soon as it's visible: not covered by another window
        (such as this console) and not under the mouse, which can change its colour."""
        announced = hover_warned = False
        for _ in range(round(COLOR_WAIT_SECONDS / COLOR_POLL_SECONDS)):
            problem, point = self._color_problem(handle, config, offset)
            if problem is None:
                self.sleep(COLOR_SETTLE_SECONDS)
                problem, point = self._color_problem(handle, config, offset)
                if problem is None:
                    return self._read_color(point, config, switch_back=announced)
            if problem == "hover" and not hover_warned:
                hover_warned = True
                self.out("  Move the mouse off the upgrade button (its colour can change while the\n"
                         "  mouse is over it)...")
            elif problem == "hidden" and not announced:
                announced = True
                self.out("  Now click the GAME on the taskbar to bring it to the front, and leave the\n"
                         "  mouse alone. I'll read the colour as soon as I can see the button...")
            self.sleep(COLOR_POLL_SECONDS)
        self.out(f"  Couldn't see the upgrade button for {COLOR_WAIT_SECONDS} seconds, so skipping it.\n"
                 "  You can add it later by double-clicking 'Calibrate Colours'.")
        return None

    def _color_problem(self, handle: int, config: Config,
                       offset: Point) -> Tuple[Optional[str], Optional[Point]]:
        if not self.backend.is_window(handle):
            raise CalibrationError("The game window was closed.")
        client = self.backend.client_rect(handle)
        if self.backend.is_minimized(handle) or client is None:
            return "hidden", None
        x, y = to_screen(offset, config.client_size, client)
        if self.backend.window_at(x, y) != handle:
            return "hidden", None
        cx, cy = self.backend.cursor_pos()
        if abs(cx - x) <= HOVER_MARGIN_PX and abs(cy - y) <= HOVER_MARGIN_PX:
            return "hover", None
        return None, (x, y)

    def _read_color(self, point: Point, config: Config, switch_back: bool) -> Optional[RGB]:
        try:
            color = self.backend.sample_color(point[0], point[1], config.sample_radius)
        except OSError as e:
            self.out(f"  Couldn't read the screen ({e}). Skipping; try 'Calibrate Colours' later.")
            return None
        self.out(f"  Got it: colour {color}.\a" + (" Now click back on this window." if switch_back else ""))
        return color

    def _warn_if_similar(self, name: str, what: str, on: Optional[RGB], off: Optional[RGB]) -> None:
        if on is not None and off is not None and distance(on, off) < 15:
            self.out(f"  Warning: the two {what} samples for '{name}' are almost identical, so the bot\n"
                     "  can't tell them apart. The spot you clicked may be on text; re-run --calibrate\n"
                     "  and click a plain part of the button.")

