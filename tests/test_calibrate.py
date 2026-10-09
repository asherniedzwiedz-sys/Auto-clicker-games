import tempfile
import unittest
from pathlib import Path

from idleclicker.backend import Rect, WindowInfo
from idleclicker.calibrate import CalibrationError, Calibrator
from idleclicker.config import Business, load_config, save_config

from .fakes import GAME, GREY, FakeBackend

GREEN = (40, 200, 60)
CONSOLE = (1200, 300)  # where the mouse is while typing answers: on the other window
UPGRADE = (350, 580)   # Oil's upgrade button on screen


class ScriptedIO:
    def __init__(self, answers):
        self.answers = list(answers)
        self.output = []

    def ask(self, prompt):
        self.output.append(prompt)
        if not self.answers:
            raise AssertionError(f"unexpected prompt: {prompt!r}")
        return self.answers.pop(0)

    def out(self, text):
        self.output.append(text)

    @property
    def text(self):
        return "\n".join(self.output)


class CalibrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "config.json"
        self.backend = FakeBackend()

    def tearDown(self):
        self.tmp.cleanup()

    def calibrator(self, answers, sleep=lambda s: None):
        self.io = ScriptedIO(answers)

        def ask(prompt):
            self.backend.cursor = CONSOLE  # the user goes to the console to type
            return self.io.ask(prompt)

        return Calibrator(self.backend, ask=ask, out=self.io.out, sleep=sleep)

    def queue_clicks(self, *points):
        for p in points:
            self.backend.queue_click(*p)

    def calibrate(self, answers, **kwargs):
        """Pick the game, click Oil's tile and upgrade button, answer the prompts."""
        self.queue_clicks((500, 300), (150, 560), (350, 580))
        cal = self.calibrator(answers, **kwargs)
        cal.calibrate(self.path)
        return load_config(self.path)

    # ---------------------------------------------------------------- positions

    def test_records_one_business_relative_to_the_window(self):
        # Game client area starts at (100, 50).
        config = self.calibrate(["y", "Example", "Oil", ""])
        self.assertEqual(config.window_title, "Example")
        self.assertEqual(config.window_class, "UnityWndClass")
        self.assertEqual(config.client_size, (800, 600))
        self.assertEqual(len(config.businesses), 1)
        oil = config.businesses[0]
        self.assertEqual((oil.name, oil.tile, oil.upgrade), ("Oil", (50, 510), (250, 530)))

    def test_rejects_clicks_outside_the_game(self):
        self.queue_clicks((500, 300),
                          (1200, 100),              # on the other window: rejected
                          (100, 20),                # above the client area (title bar): rejected
                          (150, 560), (350, 580))
        self.calibrator(["y", "", "Oil", ""]).calibrate(self.path)
        self.assertIn("wasn't on the game", self.io.text)
        self.assertEqual(load_config(self.path).businesses[0].tile, (50, 510))

    def test_rejects_taskbar_and_untitled_windows(self):
        self.backend.windows[3] = WindowInfo(3, "", "Shell_TrayWnd")
        self.backend.rects[3] = Rect(0, 1020, 1920, 60)
        self.backend.windows[4] = WindowInfo(4, "", "SomeOverlay")
        self.backend.rects[4] = Rect(1000, 600, 200, 200)
        self.queue_clicks((500, 1050),              # taskbar button: rejected
                          (1100, 700),              # untitled window: rejected
                          (500, 300),               # the game
                          (150, 560), (350, 580))
        self.calibrator(["y", "", "Oil", ""]).calibrate(self.path)
        self.assertIn("taskbar", self.io.text)
        self.assertIn("no title", self.io.text)
        self.assertEqual(load_config(self.path).window_title, "Example Clicker")

    def test_title_must_be_part_of_window_title(self):
        config = self.calibrate(["y", "Something Else", "clicker", "Oil", ""])
        self.assertEqual(config.window_title, "clicker")

    def test_window_resized_during_calibration_aborts(self):
        self.queue_clicks((500, 300), (150, 560))
        cal = self.calibrator(["y", "", "Oil"])
        real_rect = self.backend.client_rect
        calls = []

        def client_rect(handle):  # the window gets resized right after it's picked
            calls.append(handle)
            return real_rect(handle) if len(calls) == 1 else Rect(100, 50, 1024, 768)

        self.backend.client_rect = client_rect
        with self.assertRaisesRegex(CalibrationError, "changed size"):
            cal.calibrate(self.path)
        self.assertFalse(self.path.exists())

    def test_business_name_defaults(self):
        self.assertEqual(self.calibrate(["y", "", "", ""]).businesses[0].name, "My business")
        self.calibrate(["y", "", "Night Club", ""])
        self.assertEqual(self.calibrate(["y", "", "", ""]).businesses[0].name, "Night Club")

    def test_recalibrating_keeps_settings_and_replaces_the_business(self):
        config = self.calibrate(["y", "", "Oil", ""])
        config.clicks_per_second = 8
        config.businesses.append(Business("Bank", (1, 1), (2, 2)))
        save_config(config, self.path)

        self.queue_clicks((500, 300), (160, 560), (360, 580))
        self.calibrator(["y", "", "Bank", ""]).calibrate(self.path)
        config = load_config(self.path)
        self.assertEqual(config.clicks_per_second, 8)
        self.assertEqual([(b.name, b.tile) for b in config.businesses], [("Bank", (60, 510))])

    # ---------------------------------------------------------------- colours

    def test_skipping_the_colour_leaves_it_unset(self):
        oil = self.calibrate(["y", "", "Oil", ""]).businesses[0]
        self.assertIsNone(oil.upgrade_ready_color)
        self.assertIsNone(oil.upgrade_disabled_color)

    def test_lit_button_colour_is_read_straight_away_when_visible(self):
        self.backend.colors[UPGRADE] = GREEN
        oil = self.calibrate(["y", "", "Oil", "y"]).businesses[0]
        self.assertEqual(oil.upgrade_ready_color, GREEN)
        self.assertIsNone(oil.upgrade_disabled_color)
        self.assertNotIn("click the GAME on the taskbar", self.io.text)
        self.assertNotIn("click back on this window", self.io.text)

    def test_greyed_out_answer_records_disabled_colour(self):
        oil = self.calibrate(["y", "", "Oil", "n"]).businesses[0]
        self.assertEqual(oil.upgrade_disabled_color, GREY)
        self.assertIsNone(oil.upgrade_ready_color)

    def test_asks_again_on_unclear_answer(self):
        oil = self.calibrate(["y", "", "Oil", "maybe", "y"]).businesses[0]
        self.assertIn("Please answer y or n", self.io.text)
        self.assertEqual(oil.upgrade_ready_color, GREY)

    def test_waits_for_the_game_to_come_to_the_front(self):
        sleeps = []

        def sleep(seconds):  # after a few polls the user brings the game to the front
            sleeps.append(seconds)
            if len(sleeps) == 5:
                self.backend.covered.clear()

        self.queue_clicks((500, 300), (150, 560), (350, 580))
        cal = self.calibrator(["y", "", "Oil", "y"], sleep=sleep)
        original_ask = cal.ask

        def ask(prompt):
            if "lit up" in prompt:
                self.backend.covered.add(UPGRADE)  # this console sits over the button
            return original_ask(prompt)

        cal.ask = ask
        cal.calibrate(self.path)
        self.assertIn("click the GAME on the taskbar", self.io.text)
        self.assertIn("click back on this window", self.io.text)
        self.assertEqual(load_config(self.path).businesses[0].upgrade_ready_color, GREY)

    def test_waits_for_the_mouse_to_leave_the_button(self):
        sleeps = []

        def sleep(seconds):
            sleeps.append(seconds)
            if len(sleeps) == 3:
                self.backend.cursor = CONSOLE

        self.queue_clicks((500, 300), (150, 560), (350, 580))
        cal = self.calibrator(["y", "", "Oil", "y"], sleep=sleep)
        original_ask = cal.ask

        def ask(prompt):
            answer = original_ask(prompt)
            if "lit up" in prompt:
                self.backend.cursor = (352, 581)  # mouse resting on the upgrade button
            return answer

        cal.ask = ask
        cal.calibrate(self.path)
        self.assertIn("Move the mouse off", self.io.text)
        self.assertEqual(load_config(self.path).businesses[0].upgrade_ready_color, GREY)

    def test_gives_up_if_the_button_never_becomes_visible(self):
        self.queue_clicks((500, 300), (150, 560), (350, 580))
        cal = self.calibrator(["y", "", "Oil", "y"])
        original_ask = cal.ask

        def ask(prompt):
            if "lit up" in prompt:
                self.backend.covered.add(UPGRADE)
            return original_ask(prompt)

        cal.ask = ask
        cal.calibrate(self.path)
        self.assertIn("Couldn't see the upgrade button", self.io.text)
        self.assertIsNone(load_config(self.path).businesses[0].upgrade_ready_color)

    def test_recolour_keeps_existing_samples(self):
        self.backend.colors[UPGRADE] = GREEN
        config = self.calibrate(["y", "", "Oil", "y"])
        self.assertIn("Tip:", self.io.text)

        self.backend.colors[UPGRADE] = GREY
        self.calibrator(["n"]).calibrate_colors(config)
        self.assertEqual(config.businesses[0].upgrade_ready_color, GREEN)
        self.assertEqual(config.businesses[0].upgrade_disabled_color, GREY)
        self.assertNotIn("Tip:", self.io.text)

    def test_warns_when_both_samples_look_the_same(self):
        config = self.calibrate(["y", "", "Oil", "y"])
        self.calibrator(["n"]).calibrate_colors(config)
        self.assertIn("almost identical", self.io.text)

    def test_recolour_needs_the_game_running(self):
        config = self.calibrate(["y", "", "Oil", ""])
        del self.backend.windows[GAME]
        with self.assertRaisesRegex(CalibrationError, "Start the game"):
            self.calibrator([]).calibrate_colors(config)

if __name__ == "__main__":
    unittest.main()
