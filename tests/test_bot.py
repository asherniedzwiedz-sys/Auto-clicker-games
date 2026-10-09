import logging
import unittest

from idleclicker.backend import Rect
from idleclicker.bot import Bot
from idleclicker.config import Business, Config

from .fakes import (GAME, GREY, OTHER, FakeBackend, FakeClock, FakeReader, ImmediateExecutor,
                    NeverFinishesExecutor)

GREEN = (40, 200, 60)
RED = (200, 50, 50)

# Game client area is at (100, 50), 800x600, so offsets map to screen as +100, +50.
OIL_TILE, OIL_UPGRADE = (200, 550), (300, 570)
BANK_TILE, BANK_UPGRADE = (200, 450), (300, 470)


def make_config(**overrides):
    config = Config(
        window_title="Example Clicker",
        client_size=(800, 600),
        businesses=[
            Business("Oil", tile=(100, 500), upgrade=(200, 520),
                     upgrade_ready_color=GREEN, upgrade_disabled_color=GREY),
            Business("Bank", tile=(100, 400), upgrade=(200, 420),
                     upgrade_ready_color=GREEN, upgrade_disabled_color=GREY),
        ],
        user_pause_seconds=0,
    )
    for key, value in overrides.items():
        setattr(config, key, value)
    return config


class BotTestCase(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.backend = FakeBackend(self.clock)
        self.config = make_config()

    def make_bot(self, **kwargs):
        self.bot = Bot(self.config, self.backend, clock=self.clock, sleep=self.clock.sleep, **kwargs)
        return self.bot

    def run_for(self, seconds):
        """Run the real loop for `seconds` of fake time."""
        bot = getattr(self, "bot", None) or self.make_bot()
        end = self.clock.now + seconds

        def sleep(s):
            self.clock.sleep(s)
            if self.clock.now >= end:
                bot.running = False

        bot.sleep = sleep
        bot.running = True
        bot.run()
        return bot

    def clicks_at(self, point):
        return sum(1 for c in self.backend.clicks if c == point)


class TappingTests(BotTestCase):
    def test_taps_top_business_at_window_relative_point(self):
        self.run_for(1.0)
        self.assertTrue(self.backend.clicks)
        self.assertEqual(set(self.backend.clicks), {OIL_TILE})

    def test_click_rate_is_about_nine_per_second(self):
        self.run_for(10.0)
        self.assertGreaterEqual(len(self.backend.clicks), 85)
        self.assertLessEqual(len(self.backend.clicks), 92)

    def test_follows_window_when_moved(self):
        self.run_for(0.5)
        self.backend.rects[GAME] = Rect(300, 200, 800, 600)
        before = len(self.backend.clicks)
        self.run_for(0.5)
        self.assertEqual(set(self.backend.clicks[before:]), {(400, 700)})

    def test_scales_when_window_resized(self):
        self.backend.rects[GAME] = Rect(100, 50, 1600, 1200)
        with self.assertLogs("idleclicker.bot", logging.WARNING):
            self.run_for(0.5)
        self.assertEqual(set(self.backend.clicks), {(300, 1050)})

    def test_waits_for_window_then_finds_it(self):
        game = self.backend.windows.pop(GAME)
        self.run_for(1.0)
        self.assertEqual(self.backend.clicks, [])
        self.backend.windows[GAME] = game
        self.run_for(3.0)
        self.assertIn(OIL_TILE, self.backend.clicks)

    def test_title_match_is_case_insensitive_substring(self):
        self.config.window_title = "example"
        self.run_for(0.5)
        self.assertIn(OIL_TILE, self.backend.clicks)

    def test_window_class_must_match_when_set(self):
        self.config.window_class = "SomethingElse"
        self.run_for(3.0)
        self.assertEqual(self.backend.clicks, [])

    def test_dry_run_never_clicks(self):
        self.make_bot(dry_run=True)
        self.backend.colors[OIL_UPGRADE] = GREEN
        self.run_for(5.0)
        self.assertEqual(self.backend.clicks, [])
        self.assertGreater(self.bot.taps, 30)
        self.assertGreater(self.bot.upgrade_clicks, 0)


class SafetyTests(BotTestCase):
    def test_never_clicks_when_game_not_foreground(self):
        self.backend.foreground = OTHER
        self.run_for(3.0)
        self.assertEqual(self.backend.clicks, [])
        self.backend.foreground = GAME
        self.run_for(1.0)
        self.assertIn(OIL_TILE, self.backend.clicks)

    def test_stops_when_game_loses_focus(self):
        self.run_for(1.0)
        before = len(self.backend.clicks)
        self.backend.foreground = OTHER
        self.run_for(2.0)
        self.assertEqual(len(self.backend.clicks), before)

    def test_rechecks_focus_right_before_each_click(self):
        # Focus flips away between the loop's check and the click itself.
        answers = iter([GAME, OTHER] * 1000)
        self.backend.foreground_window = lambda: next(answers)
        self.run_for(2.0)
        self.assertEqual(self.backend.clicks, [])

    def test_never_clicks_when_minimized(self):
        self.backend.minimized.add(GAME)
        self.run_for(2.0)
        self.assertEqual(self.backend.clicks, [])

    def test_never_clicks_where_another_window_covers_the_game(self):
        self.backend.covered.add(OIL_TILE)
        self.run_for(2.0)
        self.assertEqual(self.backend.clicks, [])

    def test_never_clicks_covered_upgrade_button(self):
        self.backend.colors[OIL_UPGRADE] = GREEN
        self.backend.covered.add(OIL_UPGRADE)
        self.run_for(5.0)
        self.assertNotIn(OIL_UPGRADE, self.backend.clicks)
        self.assertIn(OIL_TILE, self.backend.clicks)

    def test_all_clicks_land_inside_game_client_area(self):
        self.backend.colors[OIL_UPGRADE] = GREEN
        self.run_for(10.0)
        game = self.backend.rects[GAME]
        self.assertTrue(all(game.contains(x, y) for x, y in self.backend.clicks))

    def test_stops_after_window_closes(self):
        self.run_for(0.5)
        before = len(self.backend.clicks)
        del self.backend.windows[GAME]
        self.run_for(2.0)
        self.assertEqual(len(self.backend.clicks), before)

    def test_refused_click_warns_once(self):
        self.backend.refuse_clicks = True
        with self.assertLogs("idleclicker.bot", logging.WARNING) as logs:
            self.run_for(1.0)
        self.assertEqual(sum("administrator" in line for line in logs.output), 1)


class MouseOwnershipTests(BotTestCase):
    def setUp(self):
        super().setUp()
        self.config.user_pause_seconds = 3.0

    def test_waits_for_still_mouse_when_game_gains_focus(self):
        self.run_for(2.9)
        self.assertEqual(self.backend.clicks, [])
        self.run_for(0.5)
        self.assertIn(OIL_TILE, self.backend.clicks)

    def test_backs_off_when_user_moves_mouse(self):
        self.run_for(4.0)
        before = len(self.backend.clicks)
        self.assertGreater(before, 0)
        self.backend.cursor = (500, 300)  # the user grabs the mouse
        self.run_for(2.5)
        self.assertEqual(len(self.backend.clicks), before)
        self.run_for(1.0)
        self.assertGreater(len(self.backend.clicks), before)

    def test_keeps_waiting_while_mouse_keeps_moving(self):
        self.run_for(4.0)
        before = len(self.backend.clicks)
        for i in range(5):  # the user keeps moving the mouse every second
            self.backend.cursor = (500 + 10 * i, 300)
            self.run_for(1.0)
        self.assertEqual(len(self.backend.clicks), before)


class UpgradeTests(BotTestCase):
    def test_buys_upgrade_when_affordable(self):
        self.backend.colors[OIL_UPGRADE] = GREEN
        self.run_for(5.0)  # checks at ~1s and ~4s
        self.assertEqual(self.clicks_at(OIL_UPGRADE), 2)

    def test_does_not_buy_when_not_affordable(self):
        self.run_for(10.0)
        self.assertEqual(self.clicks_at(OIL_UPGRADE), 0)

    def test_only_upgrades_the_top_business(self):
        self.backend.colors[OIL_UPGRADE] = GREEN
        self.backend.colors[BANK_UPGRADE] = GREEN
        self.run_for(10.0)
        self.assertGreater(self.clicks_at(OIL_UPGRADE), 0)
        self.assertEqual(self.clicks_at(BANK_UPGRADE), 0)
        self.assertEqual(self.clicks_at(BANK_TILE), 0)

    def test_without_colour_samples_upgrade_is_clicked_every_interval(self):
        for b in self.config.businesses:
            b.upgrade_ready_color = b.upgrade_disabled_color = None
        self.run_for(8.0)  # ~1s, ~4s, ~7s
        self.assertEqual(self.clicks_at(OIL_UPGRADE), 3)

    def test_respects_upgrade_check_interval(self):
        self.config.upgrade_check_seconds = 5.0
        self.backend.colors[OIL_UPGRADE] = GREEN
        self.run_for(10.0)  # ~1s, ~6s
        self.assertEqual(self.clicks_at(OIL_UPGRADE), 2)


class NumberUpgradeTests(BotTestCase):
    """Upgrading by the "+N" number on the upgrade button."""

    def setUp(self):
        super().setUp()
        oil = self.config.businesses[0]
        oil.number_box = (180, 500, 220, 515)  # screen (280, 550)-(320, 565)
        oil.upgrade_ready_color = oil.upgrade_disabled_color = None
        self.config.upgrade_at_plus = 5

    def make_reader_bot(self, *texts, executor=None):
        self.reader = FakeReader(*texts)
        return self.make_bot(reader=self.reader, executor=executor or ImmediateExecutor())

    def test_upgrades_when_number_reaches_threshold(self):
        self.make_reader_bot("+5")
        self.run_for(2.0)  # first check at ~1s
        self.assertEqual(self.clicks_at(OIL_UPGRADE), 1)
        self.assertGreater(self.clicks_at(OIL_TILE), 0)

    def test_does_not_upgrade_below_threshold(self):
        self.make_reader_bot("+4")
        self.run_for(10.0)
        self.assertEqual(self.clicks_at(OIL_UPGRADE), 0)
        self.assertGreater(self.clicks_at(OIL_TILE), 80)
        self.assertGreaterEqual(len(self.reader.images), 3)  # it kept checking

    def test_goes_back_to_tapping_after_upgrading(self):
        # +7 -> upgrade; it drops to +2 -> just tap; later +6 -> upgrade again.
        self.make_reader_bot("+7", "+2", "+2", "+6", "+1")
        self.run_for(12.0)
        clicks = self.backend.clicks
        upgrades = [i for i, c in enumerate(clicks) if c == OIL_UPGRADE]
        self.assertEqual(len(upgrades), 2)
        self.assertTrue(all(clicks[i + 1] == OIL_TILE for i in upgrades))  # straight back to tapping
        self.assertGreater(self.clicks_at(OIL_TILE), 90)

    def test_checks_again_soon_after_an_upgrade(self):
        # Still +5 after the first click (one click bought one level): click again ~1s later.
        self.make_reader_bot("+9", "+5", "+3")
        self.run_for(3.5)  # checks at ~1s, ~2s, ~3s
        self.assertEqual(self.clicks_at(OIL_UPGRADE), 2)

    def test_reads_the_marked_box_with_padding(self):
        self.make_reader_bot("+1")
        self.run_for(1.5)
        self.assertEqual(self.backend.grabs[0], (276, 546, 49, 24))  # (280,550)-(320,565) +/- 4px

    def test_threshold_is_configurable(self):
        self.config.upgrade_at_plus = 10
        self.make_reader_bot("+9")
        self.run_for(5.0)
        self.assertEqual(self.clicks_at(OIL_UPGRADE), 0)

    def test_unreadable_number_never_upgrades_and_warns_once(self):
        self.make_reader_bot("Level")
        with self.assertLogs("idleclicker.bot", logging.WARNING) as logs:
            self.run_for(15.0)
        self.assertEqual(self.clicks_at(OIL_UPGRADE), 0)
        self.assertEqual(sum("Can't read the + number" in line for line in logs.output), 1)

    def test_ocr_errors_never_upgrade(self):
        self.make_reader_bot(RuntimeError("OCR broke"))
        self.run_for(10.0)
        self.assertEqual(self.clicks_at(OIL_UPGRADE), 0)
        self.assertGreater(self.clicks_at(OIL_TILE), 80)

    def test_stuck_reading_is_abandoned_and_engine_restarted(self):
        self.make_reader_bot("+9", executor=NeverFinishesExecutor())
        self.run_for(25.0)
        self.assertEqual(self.clicks_at(OIL_UPGRADE), 0)
        self.assertEqual(self.reader.closed, 1)
        self.assertGreater(self.clicks_at(OIL_TILE), 200)  # tapping never stopped

    def test_no_reading_while_number_is_covered(self):
        self.backend.covered.add((276, 546))
        self.make_reader_bot("+9")
        self.run_for(5.0)
        self.assertEqual(self.backend.grabs, [])
        self.assertEqual(self.clicks_at(OIL_UPGRADE), 0)

    def test_discards_reading_when_game_loses_focus(self):
        bot = self.make_bot(reader=FakeReader("+9"), executor=NeverFinishesExecutor())
        self.run_for(1.5)
        self.assertIsNotNone(bot._pending_read)
        self.backend.foreground = OTHER
        self.run_for(0.5)
        self.assertIsNone(bot._pending_read)

    def test_without_a_reader_it_never_upgrades(self):
        with self.assertLogs("idleclicker.bot", logging.WARNING):
            self.run_for(5.0)
        self.assertEqual(self.clicks_at(OIL_UPGRADE), 0)
        self.assertGreater(self.clicks_at(OIL_TILE), 30)


class TargetSelectionTests(BotTestCase):
    def test_skips_disabled_business(self):
        self.config.businesses[0].enabled = False
        self.backend.colors[OIL_UPGRADE] = GREEN
        self.run_for(5.0)
        self.assertEqual(self.clicks_at(OIL_TILE), 0)
        self.assertEqual(self.clicks_at(OIL_UPGRADE), 0)
        self.assertGreater(self.clicks_at(BANK_TILE), 0)

    def test_skips_locked_business_and_switches_once_unlocked(self):
        oil = self.config.businesses[0]
        oil.tile_active_color, oil.tile_locked_color = RED, GREY
        self.run_for(2.0)
        self.assertEqual(self.clicks_at(OIL_TILE), 0)
        self.assertGreater(self.clicks_at(BANK_TILE), 0)
        self.backend.colors[OIL_TILE] = RED  # Oil got bought
        self.run_for(4.0)
        self.assertGreater(self.clicks_at(OIL_TILE), 0)

    def test_nothing_clicked_when_every_business_disabled(self):
        for b in self.config.businesses:
            b.enabled = False
        self.run_for(5.0)
        self.assertEqual(self.backend.clicks, [])

    def test_uses_ranker_order(self):
        class Reversed:
            description = "reversed"

            def order(self, businesses):
                return list(reversed(businesses))

        self.make_bot(ranker=Reversed())
        self.run_for(1.0)
        self.assertEqual(set(self.backend.clicks), {BANK_TILE})


class HotkeyTests(BotTestCase):
    def press(self, key):
        self.backend.keys_down.add(key)
        self.run_for(0.1)
        self.backend.keys_down.discard(key)

    def test_pause_and_resume(self):
        self.run_for(1.0)
        self.press("F8")
        before = len(self.backend.clicks)
        self.run_for(2.0)
        self.assertEqual(len(self.backend.clicks), before)
        self.press("F8")
        self.run_for(1.0)
        self.assertGreater(len(self.backend.clicks), before)

    def test_holding_pause_key_toggles_only_once(self):
        self.backend.keys_down.add("F8")
        self.run_for(1.0)
        self.assertTrue(self.bot.paused)

    def test_quit_key_stops_loop(self):
        bot = self.make_bot()
        self.backend.keys_down.add("F9")
        bot.run()  # would never return without the quit key
        self.assertFalse(bot.running)


if __name__ == "__main__":
    unittest.main()
