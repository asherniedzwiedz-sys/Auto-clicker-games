"""The main loop: tap the top-ranked business, buy its upgrade when affordable.

Safety rules, checked before every single click:
  * the game window exists, isn't minimised, and is the foreground window;
  * the point is inside the game's client area (recomputed from the window's
    current position every loop, so moving the window is fine);
  * the game is the window actually under that point (no popup/notification
    covering it).
If the user moves the mouse, the bot backs off until it has been still for
``user_pause_seconds``.
"""

from __future__ import annotations

import logging
import time
from typing import Optional, Tuple

from .backend import Backend, Point, Rect, find_window
from .colors import looks_on
from .config import Business, Config, to_screen
from .ranking import PriorityRanker

log = logging.getLogger(__name__)

# States in which the bot is actively working on a target.
ACTIVE_STATES = ("running", "no-target")


class Bot:
    WINDOW_SEARCH_SECONDS = 2.0
    POLL_SECONDS = 0.015
    MOUSE_SLOP_PX = 3

    def __init__(self, config: Config, backend: Backend, *, ranker=None, dry_run: bool = False,
                 clock=time.monotonic, sleep=time.sleep) -> None:
        self.config = config
        self.backend = backend
        self.ranker = ranker or PriorityRanker()
        self.dry_run = dry_run
        self.clock = clock
        self.sleep = sleep
        self.click_interval = 1.0 / config.clicks_per_second
        self.hold_seconds = config.click_hold_ms / 1000

        self.running = True
        self.paused = False
        self.handle: Optional[int] = None
        self.target: Optional[Business] = None
        self.taps = 0
        self.upgrade_clicks = 0

        self._state: Optional[str] = None
        self._status_message: Optional[str] = None
        self._next_window_search = 0.0
        self._next_check = 0.0
        self._had_focus = False
        self._expected_cursor: Optional[Point] = None
        self._last_cursor: Optional[Point] = None
        self._hands_off_until = 0.0
        self._keys_held = set()
        self._warned_blocked = False
        self._warned_size: Optional[Tuple[int, int]] = None

    # ------------------------------------------------------------ loop

    def run(self) -> None:
        self._log_plan()
        try:
            while self.running:
                deadline = self.clock() + self.click_interval
                self.tick()
                self._wait_until(deadline)
        finally:
            log.info("Stopped after %d taps and %d upgrade clicks.", self.taps, self.upgrade_clicks)

    def _wait_until(self, deadline: float) -> None:
        while True:
            self._poll_hotkeys()
            if not self.running:
                return
            remaining = deadline - self.clock()
            if remaining <= 0:
                return
            self.sleep(min(self.POLL_SECONDS, remaining))

    def tick(self) -> None:
        """One iteration: at most one click."""
        now = self.clock()
        cfg = self.config
        if self.paused:
            self._lose_focus()
            self._set_status("paused", f"Paused. Press {cfg.pause_key} to resume, {cfg.quit_key} to quit.")
            return
        handle = self._find_window(now)
        if handle is None:
            self._lose_focus()
            self._set_status("no-window", f"Waiting for the game window '{cfg.window_title}'...")
            return
        if self.backend.is_minimized(handle):
            self._lose_focus()
            self._set_status("minimized", "The game is minimised; waiting.")
            return
        if self.backend.foreground_window() != handle:
            self._lose_focus()
            self._set_status("background", "The game isn't the active window, so not clicking. "
                                           "Click on the game to resume.")
            return
        client = self.backend.client_rect(handle)
        if client is None or client.width <= 0 or client.height <= 0:
            self._set_status("no-client", "Can't read the game window's size; waiting.")
            return
        self._check_size(client)
        if self._mouse_in_use(now):
            self._set_status("hands-off", f"Waiting for the mouse to be still for "
                                          f"{cfg.user_pause_seconds:g}s before clicking...")
            return

        if self._state not in ACTIVE_STATES:
            # (Re)starting. The first upgrade check waits a moment so the first taps
            # have parked the cursor on the tile, away from the upgrade button.
            self.target = self._choose_target(client)
            self._next_check = now + min(1.0, cfg.upgrade_check_seconds)
        elif now >= self._next_check:
            self._next_check = now + cfg.upgrade_check_seconds
            self.target = self._choose_target(client)
            if self.target is not None and self._maybe_buy_upgrade(handle, client):
                return

        if self.target is None:
            self._set_status("no-target", "No business to click: all are disabled or look locked.")
            return
        x, y = to_screen(self.target.tile, cfg.client_size, client)
        if self.backend.window_at(x, y) != handle:
            self._set_status("covered", "Another window is covering the game; waiting.")
            return
        self._set_status("running", f"Tapping '{self.target.name}' {cfg.clicks_per_second:g}x/s and "
                                    f"buying its upgrades. {cfg.pause_key} = pause, {cfg.quit_key} = quit.")
        if self._click(handle, client, self.target.tile):
            self.taps += 1

    # ------------------------------------------------------------ hotkeys

    def _poll_hotkeys(self) -> None:
        for key, action in ((self.config.pause_key, self.toggle_pause),
                            (self.config.quit_key, self.stop)):
            if self.backend.is_key_down(key):
                if key not in self._keys_held:
                    self._keys_held.add(key)
                    action()
            else:
                self._keys_held.discard(key)

    def toggle_pause(self) -> None:
        self.paused = not self.paused
        if not self.paused:
            log.info("Resumed.")

    def stop(self) -> None:
        log.info("Quit key pressed.")
        self.running = False

    # ------------------------------------------------------------ window

    def _find_window(self, now: float) -> Optional[int]:
        if self.handle is not None:
            if self.backend.is_window(self.handle):
                return self.handle
            log.info("The game window was closed.")
            self.handle = None
            self.target = None
        if now < self._next_window_search:
            return None
        self._next_window_search = now + self.WINDOW_SEARCH_SECONDS
        cfg = self.config
        info = find_window(self.backend, cfg.window_title, cfg.window_class, cfg.title_match)
        if info is not None:
            log.info("Found the game window: '%s'.", info.title)
            self.handle = info.handle
        return self.handle

    def _check_size(self, client: Rect) -> None:
        size = (client.width, client.height)
        if size == tuple(self.config.client_size) or size == self._warned_size:
            return
        self._warned_size = size
        cal_w, cal_h = self.config.client_size
        log.warning("The game window is %dx%d but was calibrated at %dx%d; scaling click positions "
                    "to match. If clicks miss, resize the window back or re-run --calibrate.",
                    client.width, client.height, cal_w, cal_h)

    # ------------------------------------------------------------ mouse ownership

    def _lose_focus(self) -> None:
        self._had_focus = False
        self._expected_cursor = None

    def _hands_off(self, now: float, pos: Point) -> None:
        self._hands_off_until = now + self.config.user_pause_seconds
        self._last_cursor = pos
        self._expected_cursor = None

    def _far(self, a: Point, b: Optional[Point]) -> bool:
        return b is None or max(abs(a[0] - b[0]), abs(a[1] - b[1])) > self.MOUSE_SLOP_PX

    def _mouse_in_use(self, now: float) -> bool:
        """True while the user is (probably) using the mouse, so we must not grab it."""
        pos = self.backend.cursor_pos()
        if not self._had_focus:
            # The game just became active, or we just resumed: the user's hand is
            # probably still on the mouse. Wait for it to be still first.
            self._had_focus = True
            self._hands_off(now, pos)
            return now < self._hands_off_until
        if self._expected_cursor is not None:
            if self._far(pos, self._expected_cursor):
                self._hands_off(now, pos)
                return now < self._hands_off_until
            return False
        if self._far(pos, self._last_cursor):
            self._hands_off(now, pos)
        return now < self._hands_off_until

    # ------------------------------------------------------------ businesses

    def _probe(self, client: Rect, offset: Point, on, off, default: bool) -> bool:
        x, y = to_screen(offset, self.config.client_size, client)
        try:
            color = self.backend.sample_color(x, y, self.config.sample_radius)
        except OSError as e:
            log.warning("Couldn't read the screen colour at (%d, %d): %s", x, y, e)
            return default
        result = looks_on(color, on, off, self.config.color_tolerance, default)
        log.debug("Colour at (%d, %d) is %s (on=%s off=%s) -> %s", x, y, color, on, off, result)
        return result

    def _choose_target(self, client: Rect) -> Optional[Business]:
        for business in self.ranker.order(self.config.businesses):
            if not business.enabled:
                continue
            if business.has_tile_colors and not self._probe(
                    client, business.tile, business.tile_active_color,
                    business.tile_locked_color, default=True):
                log.debug("Skipping '%s': it looks locked.", business.name)
                continue
            return business
        return None

    def _maybe_buy_upgrade(self, handle: int, client: Rect) -> bool:
        """Click the target's upgrade button if it looks affordable. True if clicked."""
        business = self.target
        if not self._probe(client, business.upgrade, business.upgrade_ready_color,
                           business.upgrade_disabled_color, default=True):
            log.debug("Upgrade for '%s' isn't affordable yet.", business.name)
            return False
        if not self._click(handle, client, business.upgrade):
            return False
        self.upgrade_clicks += 1
        if business.has_upgrade_colors:
            log.info("Bought an upgrade for '%s'.", business.name)
        else:
            log.debug("Clicked the upgrade button for '%s'.", business.name)
        return True

    # ------------------------------------------------------------ clicking

    def _click(self, handle: int, client: Rect, offset: Point) -> bool:
        x, y = to_screen(offset, self.config.client_size, client)
        # Re-check everything right before clicking: never click outside the game.
        if not client.contains(x, y):
            return False
        if self.backend.foreground_window() != handle:
            return False
        if self.backend.window_at(x, y) != handle:
            log.debug("Something is covering the game at (%d, %d); not clicking.", x, y)
            return False
        if self.dry_run:
            log.debug("[dry run] would click at (%d, %d)", x, y)
            return True
        if not self.backend.click(x, y, self.hold_seconds):
            if not self._warned_blocked:
                self._warned_blocked = True
                log.warning("Windows refused to send the click. This happens when the game runs "
                            "as administrator and this script doesn't.")
            return False
        self._expected_cursor = (x, y)
        return True

    # ------------------------------------------------------------ logging

    def _set_status(self, state: str, message: str) -> None:
        self._state = state
        if message != self._status_message:
            self._status_message = message
            log.info(message)

    def _log_plan(self) -> None:
        cfg = self.config
        log.info("Looking for the game window titled '%s'%s.", cfg.window_title,
                 f" (class {cfg.window_class})" if cfg.window_class else "")
        log.info("Businesses, best first (%s):", self.ranker.description)
        for i, b in enumerate(cfg.businesses, 1):
            notes = []
            if not b.enabled:
                notes.append("disabled")
            elif not b.has_upgrade_colors:
                notes.append("no upgrade colours sampled: its upgrade button is just clicked "
                             f"every {cfg.upgrade_check_seconds:g}s")
            log.info("  %d. %s%s", i, b.name, f"  ({'; '.join(notes)})" if notes else "")
        log.info("%s = pause/resume, %s = quit (or Ctrl+C here). Moving the mouse pauses "
                 "clicking until it's still again.", cfg.pause_key, cfg.quit_key)
        if self.dry_run:
            log.info("DRY RUN: nothing will actually be clicked.")
