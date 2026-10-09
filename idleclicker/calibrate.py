"""--calibrate, --calibrate-colors and --check: the interactive set-up walkthrough.

Clicks and Ctrl presses are detected by polling the button/key state
(GetAsyncKeyState), so no mouse or keyboard hook is installed. Clicks still reach
the game as normal clicks.
"""

from __future__ import annotations

import dataclasses
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

from .backend import RGB, Backend, Point, WindowInfo, find_window
from .colors import distance, looks_on
from .config import (Box, Business, Config, ConfigError, box_to_screen, load_config, save_config,
                     to_screen)
from .winocr import OcrUnavailable, encode_bmp, parse_plus_number, upscale_factor

HOVER_MARGIN_PX = 30
COLOR_WAIT_SECONDS = 60     # how long to wait for the upgrade button to become visible
COLOR_POLL_SECONDS = 0.1
COLOR_SETTLE_SECONDS = 0.5  # let the game finish redrawing after it comes to the front
NUMBER_BOX_PADDING_PX = 4   # same as the bot uses
OCR_TIMEOUT_SECONDS = 45    # the first read also starts Windows' text recognition
# Windows' own taskbar and desktop windows: never the game.
SHELL_WINDOW_CLASSES = {"Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Progman", "WorkerW"}


class CalibrationError(Exception):
    pass


class Calibrator:
    def __init__(self, backend: Backend, *, ask: Callable[[str], str] = input,
                 out: Callable[[str], None] = print, sleep=time.sleep, reader=None) -> None:
        self.backend = backend
        self.ask = ask
        self.out = out
        self.sleep = sleep
        self.reader = reader  # reads the "+N" number (winocr.WindowsOcr); None if unavailable

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
        return self._wait_for_press(self.backend.is_mouse_down)

    def wait_for_ctrl(self) -> Point:
        """Block until Ctrl is pressed and released; return where the mouse pointer was."""
        return self._wait_for_press(lambda: self.backend.is_key_down("CTRL"))

    def _wait_for_press(self, is_down: Callable[[], bool]) -> Point:
        while is_down():  # still held from before
            self.sleep(0.01)
        while not is_down():
            self.sleep(0.01)
        pos = self.backend.cursor_pos()
        while is_down():
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
        use_number = self.reader is not None and self.yes_no(
            f"\nDoes the upgrade button for '{name}' show a + number (like +3)?", True)

        self.out("\nStep 3: bring the game to the front and do these in order. You'll hear a beep\n"
                 "after each one.\n"
                 f"  1. CLICK the business to tap ('{name}'). Click the middle of it.\n"
                 "  2. CLICK its upgrade button.")
        if use_number:
            self.out("  3. POINT (don't click) at the TOP-LEFT corner of the + number, press Ctrl.\n"
                     "  4. POINT at the BOTTOM-RIGHT corner of the + number, press Ctrl.\n"
                     "     Mark just the + and its digits, not the word 'Level' or other numbers.")
        tile = self._capture(window.handle, size, "  Waiting for 1 (click the business)...")
        upgrade = self._capture(window.handle, size, "  Waiting for 2 (click the upgrade button)...")
        box = self._mark_number_box(window.handle, size) if use_number else None
        business = Business(name=name, tile=tile, upgrade=upgrade, number_box=box)

        if previous is not None:
            config = dataclasses.replace(previous, window_title=title, window_class=window.class_name,
                                         client_size=size, businesses=[business])
        else:
            config = Config(window_title=title, window_class=window.class_name,
                            client_size=size, businesses=[business])

        if box is not None:
            self.out("\nStep 4: checking that it can read the + number.")
            business.number_box = self._confirm_number_box(window.handle, config, box)
        if business.number_box is not None:
            config.upgrade_at_plus = self._ask_threshold(config.upgrade_at_plus)
        else:
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

    def _capture(self, handle: int, size: Tuple[int, int], prompt: str,
                 wait: Optional[Callable[[], Point]] = None) -> Point:
        """Wait for a click (or ``wait``) on the game; return its offset in the client area."""
        self.out(prompt)
        while True:
            pos = (wait or self.wait_for_click)()
            if self.backend.window_at(*pos) != handle:
                self.out("  That wasn't on the game. Bring the game to the front and try again.")
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

    # ------------------------------------------------------------ the "+N" number

    def _mark_number_box(self, handle: int, size: Tuple[int, int]) -> Box:
        while True:
            left, top = self._capture(handle, size, "  Waiting for 3 (point at the TOP-LEFT of the "
                                      "+ number, press Ctrl)...", wait=self.wait_for_ctrl)
            right, bottom = self._capture(handle, size, "  Waiting for 4 (point at the BOTTOM-RIGHT "
                                          "of the + number, press Ctrl)...", wait=self.wait_for_ctrl)
            if right - left >= 3 and bottom - top >= 3:
                return left, top, right, bottom
            self.out("  The second point has to be below and to the right of the first. Again:")

    def _confirm_number_box(self, handle: int, config: Config, box: Box) -> Optional[Box]:
        """Read the number back to the user until they confirm it. None to give up on it."""
        while True:
            try:
                text, value = self._read_number(handle, config, box)
            except OcrUnavailable as e:
                self.out(f"  {e}\n  So the bot will go by the upgrade button's colour instead.")
                return None
            if value is not None:
                if self.yes_no(f"  It reads {value}. Is that the + number on the button right now?", True):
                    return box
            elif text is not None:
                self.out(f"  It couldn't find a number there (it saw {text!r}).")
            if not self.yes_no("Mark the + number again?", True):
                self.out("  OK. The bot will go by the upgrade button's colour instead.")
                return None
            self.out("  Bring the game to the front, then:")
            box = self._mark_number_box(handle, config.client_size)

    def _read_number(self, handle: int, config: Config, box: Box,
                     save_to: Optional[Path] = None) -> Tuple[Optional[str], Optional[int]]:
        """Read the "+N" number once it's visible. (None, None) if it never became visible."""
        if self._wait_visible(handle, config.client_size, [box[:2], box[2:]], "the + number") is None:
            return None, None
        client = self.backend.client_rect(handle)
        left, top, right, bottom = box_to_screen(box, config.client_size, client, NUMBER_BOX_PADDING_PX)
        try:
            image = self.backend.grab(left, top, right - left + 1, bottom - top + 1)
        except OSError as e:
            raise OcrUnavailable(f"Couldn't capture the screen: {e}") from None
        if save_to is not None:
            Path(save_to).write_bytes(encode_bmp(image, upscale_factor(image)))
        pool = ThreadPoolExecutor(max_workers=1)
        try:
            text = pool.submit(self.reader.read, image).result(timeout=OCR_TIMEOUT_SECONDS)
        except FutureTimeout:
            self.reader.close()
            raise OcrUnavailable("Windows text recognition took too long to answer.") from None
        finally:
            pool.shutdown(wait=False)
        return text, parse_plus_number(text)

    def _ask_threshold(self, default: int) -> int:
        self.out("\nThe bot keeps tapping, and clicks upgrade once the + number gets high enough.")
        while True:
            answer = self.ask(f"Upgrade when the + number is at least [Enter = {default}]: ").strip()
            if not answer:
                return default
            if answer.isdigit() and int(answer) >= 1:
                return int(answer)
            self.out("  Please type a whole number, like 5.")

    # ------------------------------------------------------------ check setup

    def check(self, config: Config, picture: Path = Path("number_check.bmp")) -> None:
        """Show where the bot taps and upgrades, and what it reads, without clicking anything."""
        window = find_window(self.backend, config.window_title, config.window_class, config.title_match)
        if window is None:
            raise CalibrationError(f"Can't find the game window '{config.window_title}'. "
                                   "Start the game first.")
        business = next((b for b in config.businesses if b.enabled), None)
        if business is None:
            raise CalibrationError("No business is enabled in the config file.")
        self.out("\n=== Check Setup (nothing gets clicked) ===\n"
                 "Click on the game to bring it to the front, let go of the mouse, and watch the\n"
                 "pointer: it moves to the spot it TAPS, then to the UPGRADE button.")
        if not self._wait_for_foreground(window.handle):
            self.out("The game didn't come to the front within a minute, so stopping.")
            return
        self.sleep(1.0)
        for label, offset in (("TAP spot", business.tile), ("UPGRADE button", business.upgrade)):
            if self.backend.foreground_window() != window.handle:
                self.out("The game isn't in front any more, so stopping.")
                return
            x, y = to_screen(offset, config.client_size, self.backend.client_rect(window.handle))
            self.backend.move_cursor(x, y)
            self.out(f"  The pointer is on the {label} for '{business.name}'.")
            self.sleep(2.0)
        x, y = to_screen(business.tile, config.client_size, self.backend.client_rect(window.handle))
        self.backend.move_cursor(x, y)  # off the upgrade button before looking at it

        if business.number_box is not None:
            if self.reader is None:
                self.out("  Windows text recognition isn't available, so it can't read the + number.")
            else:
                self._check_number(window.handle, config, business, picture)
        elif business.has_upgrade_colors:
            visible = self._wait_visible(window.handle, config.client_size, [business.upgrade],
                                         "the upgrade button")
            if visible is not None:
                (x, y), = visible[0]
                color = self.backend.sample_color(x, y, config.sample_radius)
                ready = looks_on(color, business.upgrade_ready_color, business.upgrade_disabled_color,
                                 config.color_tolerance, default=True)
                self.out(f"  The upgrade button {'looks' if ready else 'does NOT look'} affordable "
                         f"right now (colour {color}).")
        else:
            self.out(f"  It clicks the upgrade button every {config.upgrade_check_seconds:g} seconds.")
        self.out("Done. Click back on this window.")

    def _check_number(self, handle: int, config: Config, business: Business, picture: Path) -> None:
        try:
            text, value = self._read_number(handle, config, business.number_box, save_to=picture)
        except OcrUnavailable as e:
            self.out(f"  {e}")
            return
        if text is None:
            return
        threshold = config.upgrade_at_plus
        if value is None:
            self.out(f"  It can't find a number in the + number box (it saw {text!r}).")
        else:
            verdict = "WOULD upgrade" if value >= threshold else "would keep tapping"
            self.out(f"  The + number reads {value}, so right now it {verdict} "
                     f"(it upgrades at {threshold} or more).")
        self.out(f"  The picture it read is saved as {picture}.")

    def _wait_for_foreground(self, handle: int) -> bool:
        for _ in range(round(COLOR_WAIT_SECONDS / COLOR_POLL_SECONDS)):
            if self.backend.foreground_window() == handle:
                return True
            self.sleep(COLOR_POLL_SECONDS)
        return False

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
        visible = self._wait_visible(handle, config.client_size, [offset], "the upgrade button")
        if visible is None:
            return None
        (x, y), = visible[0]
        try:
            color = self.backend.sample_color(x, y, config.sample_radius)
        except OSError as e:
            self.out(f"  Couldn't read the screen ({e}). Skipping; try 'Calibrate Colours' later.")
            return None
        self.out(f"  Saved colour {color}.")
        return color

    def _wait_visible(self, handle: int, client_size: Tuple[int, int], offsets: Sequence[Point],
                      what: str) -> Optional[Tuple[List[Point], bool]]:
        """Wait until every point is visible on the game (not covered by another window such
        as this console) and the mouse isn't over them, since hovering can change how things
        look. Returns the screen points and whether the user had to bring the game forward,
        or None after a minute."""
        announced = hover_warned = False
        for _ in range(round(COLOR_WAIT_SECONDS / COLOR_POLL_SECONDS)):
            problem, points = self._visibility(handle, client_size, offsets)
            if problem is None:
                self.sleep(COLOR_SETTLE_SECONDS)
                problem, points = self._visibility(handle, client_size, offsets)
                if problem is None:
                    if announced:
                        self.out("  Got it.\a Now click back on this window.")
                    return points, announced
            if problem == "hover" and not hover_warned:
                hover_warned = True
                self.out(f"  Move the mouse off {what} (the way it looks can change while the\n"
                         "  mouse is over it)...")
            elif problem == "hidden" and not announced:
                announced = True
                self.out("  Now click the GAME on the taskbar to bring it to the front, and leave the\n"
                         f"  mouse alone. I'll look at {what} as soon as I can see it...")
            self.sleep(COLOR_POLL_SECONDS)
        self.out(f"  Couldn't see {what} for {COLOR_WAIT_SECONDS} seconds, so skipping it.\n"
                 "  You can add it later by double-clicking 'Calibrate Colours'."
                 if what == "the upgrade button" else
                 f"  Couldn't see {what} for {COLOR_WAIT_SECONDS} seconds.")
        return None

    def _visibility(self, handle: int, client_size: Tuple[int, int],
                    offsets: Sequence[Point]) -> Tuple[Optional[str], List[Point]]:
        if not self.backend.is_window(handle):
            raise CalibrationError("The game window was closed.")
        client = self.backend.client_rect(handle)
        if self.backend.is_minimized(handle) or client is None:
            return "hidden", []
        points = [to_screen(offset, client_size, client) for offset in offsets]
        if any(self.backend.window_at(x, y) != handle for x, y in points):
            return "hidden", []
        cx, cy = self.backend.cursor_pos()
        xs, ys = [x for x, _ in points], [y for _, y in points]
        if (min(xs) - HOVER_MARGIN_PX <= cx <= max(xs) + HOVER_MARGIN_PX
                and min(ys) - HOVER_MARGIN_PX <= cy <= max(ys) + HOVER_MARGIN_PX):
            return "hover", []
        return None, points

    def _warn_if_similar(self, name: str, what: str, on: Optional[RGB], off: Optional[RGB]) -> None:
        if on is not None and off is not None and distance(on, off) < 15:
            self.out(f"  Warning: the two {what} samples for '{name}' are almost identical, so the bot\n"
                     "  can't tell them apart. The spot you clicked may be on text; re-run --calibrate\n"
                     "  and click a plain part of the button.")

