"""--calibrate and --calibrate-colors: the interactive set-up walkthrough.

Clicks are detected by polling the mouse button state (GetAsyncKeyState), so no
mouse hook is installed. The clicks still reach the game as normal clicks.
"""

from __future__ import annotations

import dataclasses
import time
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from .backend import RGB, Backend, Point, WindowInfo, find_window
from .colors import distance
from .config import Business, Config, ConfigError, load_config, save_config, to_screen

HOVER_MARGIN_PX = 30
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
            "  * Put this console next to the game so you can read it while clicking.\n"
            "  * Your clicks go through to the game as normal: tapping a business is\n"
            "    harmless, and clicking an upgrade button may buy it.\n"
            "Press Ctrl+C at any time to cancel without changing anything.\n")

        window, size = self._pick_window()
        title = self._ask_title(window)
        names = self._ask_names(previous)

        self.out("\nStep 3: you'll now click, in this order:")
        for i, name in enumerate(names, 1):
            self.out(f"  {2 * i - 1}. the business tile for '{name}'\n"
                     f"  {2 * i}. the upgrade button for '{name}'")
        self.out("Tip: click the middle of each button.\n")

        businesses = []
        for i, name in enumerate(names, 1):
            tag = f"[{i}/{len(names)}]"
            tile = self._capture(window.handle, size, f"{tag} Click the business TILE for '{name}'...")
            upgrade = self._capture(window.handle, size, f"{tag} Click the UPGRADE button for '{name}'...")
            old = _by_name(previous, name)
            businesses.append(Business(name=name, tile=tile, upgrade=upgrade,
                                       enabled=old.enabled if old else True))

        if previous is not None:
            config = dataclasses.replace(previous, window_title=title, window_class=window.class_name,
                                         client_size=size, businesses=businesses)
        else:
            config = Config(window_title=title, window_class=window.class_name,
                            client_size=size, businesses=businesses)

        self.out("\nStep 4 (recommended): teach the bot what an AFFORDABLE upgrade button looks like.\n"
                 "Without this it simply clicks the upgrade button every few seconds, which is\n"
                 "harmless if the game ignores clicks on buttons you can't afford.")
        if self.yes_no("Sample the button colours now?", True):
            self.calibrate_colors(config)

        backup = save_config(config, path)
        self.out(f"\nSaved {path}" + (f" (previous version kept as {backup.name})" if backup else "") + ".")
        self.out("Next: run with --dry-run -v to check it, then run normally.\n"
                 "To re-rank businesses later, reorder them in config.json (highest earner first)\n"
                 'or set "enabled": false on ones you want skipped.')
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

    def _ask_names(self, previous: Optional[Config]) -> List[str]:
        self.out("\nStep 2: list the businesses for the bot to use, HIGHEST EARNER FIRST.\n"
                 "The bot always works on the first one in the list (skipping any you disable).")
        if previous is not None and previous.businesses:
            self.out("Current list: " + ", ".join(b.name for b in previous.businesses))
            if self.yes_no("Keep this list and order?", True):
                return [b.name for b in previous.businesses]
        self.out("Type one name per line, then press Enter on an empty line to finish.")
        names: List[str] = []
        while True:
            name = self.ask(f"  {len(names) + 1}. ").strip()
            if not name:
                if names:
                    return names
                self.out("  Enter at least one business.")
            elif name.casefold() in (n.casefold() for n in names):
                self.out("  Already listed.")
            else:
                names.append(name)

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
        """Sample upgrade (and optionally tile) colours for each business, in place."""
        window = find_window(self.backend, config.window_title, config.window_class, config.title_match)
        if window is None:
            raise CalibrationError(f"Can't find the game window '{config.window_title}'. "
                                   "Start the game first.")
        self.out(
            "\nColour sampling. For each business you'll be asked about its current state,\n"
            "and the bot reads the colour on screen as you answer. So:\n"
            "  * arrange this console so it does NOT cover the game's buttons;\n"
            "  * keep the mouse off the game's buttons;\n"
            "  * answer by what you see right now. Press Enter to skip one.\n"
            "Lit-up and greyed-out samples are both useful: you can run --calibrate-colors\n"
            "again later to add whichever state you skipped.")
        locks = self.yes_no("Did you list any business you haven't unlocked (bought) yet?", False)
        for b in config.businesses:
            state, color = self._ask_state(
                window.handle, config, b.upgrade,
                f"'{b.name}': is its UPGRADE button lit up (affordable) right now? [y/n, Enter = skip] ")
            if state is True:
                b.upgrade_ready_color = color
            elif state is False:
                b.upgrade_disabled_color = color
            self._warn_if_similar(b.name, "upgrade", b.upgrade_ready_color, b.upgrade_disabled_color)
            if locks:
                state, color = self._ask_state(
                    window.handle, config, b.tile,
                    f"'{b.name}': is this business unlocked (owned)? [y/n, Enter = skip] ")
                if state is True:
                    b.tile_active_color = color
                elif state is False:
                    b.tile_locked_color = color
                self._warn_if_similar(b.name, "tile", b.tile_active_color, b.tile_locked_color)

    def _ask_state(self, handle: int, config: Config, offset: Point,
                   question: str) -> Tuple[Optional[bool], Optional[RGB]]:
        while True:
            answer = self.ask(question).strip().lower()
            if answer in ("", "s", "skip"):
                return None, None
            if answer not in ("y", "yes", "n", "no"):
                self.out("  Please answer y or n, or press Enter to skip.")
                continue
            color = self._sample(handle, config, offset)
            if color is not None:
                return answer.startswith("y"), color

    def _sample(self, handle: int, config: Config, offset: Point) -> Optional[RGB]:
        if not self.backend.is_window(handle):
            raise CalibrationError("The game window was closed.")
        client = self.backend.client_rect(handle)
        if self.backend.is_minimized(handle) or client is None:
            self.out("  The game is minimised. Restore it, then answer again.")
            return None
        x, y = to_screen(offset, config.client_size, client)
        if self.backend.window_at(x, y) != handle:
            self.out("  Another window (maybe this console) covers that button. Move it out of\n"
                     "  the way, then answer again.")
            return None
        cx, cy = self.backend.cursor_pos()
        if abs(cx - x) <= HOVER_MARGIN_PX and abs(cy - y) <= HOVER_MARGIN_PX:
            self.out("  The mouse is over that button, which can change its colour. Move it\n"
                     "  away, then answer again.")
            return None
        try:
            color = self.backend.sample_color(x, y, config.sample_radius)
        except OSError as e:
            self.out(f"  Couldn't read the screen ({e}). Answer again to retry.")
            return None
        self.out(f"  Sampled colour {color}.")
        return color

    def _warn_if_similar(self, name: str, what: str, on: Optional[RGB], off: Optional[RGB]) -> None:
        if on is not None and off is not None and distance(on, off) < 15:
            self.out(f"  Warning: the two {what} samples for '{name}' are almost identical, so the bot\n"
                     "  can't tell them apart. The spot you clicked may be on text; re-run --calibrate\n"
                     "  and click a plain part of the button.")


def _by_name(config: Optional[Config], name: str) -> Optional[Business]:
    if config is None:
        return None
    for b in config.businesses:
        if b.name.casefold() == name.casefold():
            return b
    return None
