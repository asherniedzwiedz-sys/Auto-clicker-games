import tempfile
import unittest
from pathlib import Path

from idleclicker.backend import Rect, WindowInfo
from idleclicker.calibrate import CalibrationError, Calibrator
from idleclicker.config import load_config, save_config

from .fakes import GAME, GREY, FakeBackend

GREEN = (40, 200, 60)
CONSOLE = (1200, 300)  # where the mouse is while typing answers: on the other window


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

    def calibrator(self, answers):
        self.io = ScriptedIO(answers)

        def ask(prompt):
            self.backend.cursor = CONSOLE  # the user goes to the console to type
            return self.io.ask(prompt)

        return Calibrator(self.backend, ask=ask, out=self.io.out, sleep=lambda s: None)

    def queue_clicks(self, *points):
        for p in points:
            self.backend.queue_click(*p)

    def test_full_calibration_records_window_relative_offsets(self):
        # Game client area starts at (100, 50).
        self.queue_clicks((500, 300),               # pick the game window
                          (150, 560), (350, 580),   # Oil tile, upgrade
                          (150, 460), (350, 480))   # Bank tile, upgrade
        cal = self.calibrator(["y", "Example", "Oil", "Bank", "", "n"])
        cal.calibrate(self.path)

        config = load_config(self.path)
        self.assertEqual(config.window_title, "Example")
        self.assertEqual(config.window_class, "UnityWndClass")
        self.assertEqual(config.client_size, (800, 600))
        self.assertEqual([b.name for b in config.businesses], ["Oil", "Bank"])
        self.assertEqual(config.businesses[0].tile, (50, 510))
        self.assertEqual(config.businesses[0].upgrade, (250, 530))
        self.assertEqual(config.businesses[1].tile, (50, 410))
        self.assertEqual(config.businesses[1].upgrade, (250, 430))

    def test_rejects_clicks_outside_the_game(self):
        self.queue_clicks((500, 300),
                          (1200, 100),              # on the other window: rejected
                          (100, 20),                # above the client area (title bar): rejected
                          (150, 560), (350, 580))
        cal = self.calibrator(["y", "", "Oil", "", "n"])
        cal.calibrate(self.path)
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
        self.calibrator(["y", "", "Oil", "", "n"]).calibrate(self.path)
        self.assertIn("taskbar", self.io.text)
        self.assertIn("no title", self.io.text)
        self.assertEqual(load_config(self.path).window_title, "Example Clicker")

    def test_title_must_be_part_of_window_title(self):
        self.queue_clicks((500, 300), (150, 560), (350, 580))
        cal = self.calibrator(["y", "Something Else", "clicker", "Oil", "", "n"])
        cal.calibrate(self.path)
        self.assertEqual(load_config(self.path).window_title, "clicker")

    def test_window_resized_during_calibration_aborts(self):
        self.queue_clicks((500, 300), (150, 560))
        cal = self.calibrator(["y", "", "Oil", ""])
        real_rect = self.backend.client_rect
        calls = []

        def client_rect(handle):  # the window gets resized right after it's picked
            calls.append(handle)
            return real_rect(handle) if len(calls) == 1 else Rect(100, 50, 1024, 768)

        self.backend.client_rect = client_rect
        with self.assertRaisesRegex(CalibrationError, "changed size"):
            cal.calibrate(self.path)
        self.assertFalse(self.path.exists())

    def test_keeps_previous_list_and_settings(self):
        self.queue_clicks((500, 300), (150, 560), (350, 580), (150, 460), (350, 480))
        self.calibrator(["y", "", "Oil", "Bank", "", "n"]).calibrate(self.path)
        config = load_config(self.path)
        config.clicks_per_second = 8
        config.businesses[1].enabled = False
        save_config(config, self.path)

        self.queue_clicks((500, 300), (160, 560), (360, 580), (160, 460), (360, 480))
        self.calibrator(["y", "", "y", "n"]).calibrate(self.path)
        config = load_config(self.path)
        self.assertEqual(config.clicks_per_second, 8)
        self.assertFalse(config.businesses[1].enabled)
        self.assertEqual(config.businesses[0].tile, (60, 510))

    def test_colour_sampling(self):
        self.queue_clicks((500, 300), (150, 560), (350, 580), (150, 460), (350, 480))
        self.backend.colors[(350, 580)] = GREEN
        # y = sample colours, n = no locked businesses, Oil upgrade lit: y, Bank: n
        self.calibrator(["y", "", "Oil", "Bank", "", "y", "n", "y", "n"]).calibrate(self.path)
        oil, bank = load_config(self.path).businesses
        self.assertEqual(oil.upgrade_ready_color, GREEN)
        self.assertIsNone(oil.upgrade_disabled_color)
        self.assertEqual(bank.upgrade_disabled_color, GREY)
        self.assertIsNone(bank.upgrade_ready_color)

    def test_colour_sampling_waits_until_button_is_visible_and_not_hovered(self):
        self.queue_clicks((500, 300), (150, 560), (350, 580))
        cal = self.calibrator(["y", "", "Oil", "", "y", "n", "y", "y", "y"])
        original_ask = cal.ask
        asked = []

        def ask(prompt):
            answer = original_ask(prompt)
            if "UPGRADE" in prompt:
                asked.append(prompt)
                if len(asked) == 1:
                    self.backend.covered.add((350, 580))  # the console covers the button
                elif len(asked) == 2:
                    self.backend.covered.clear()
                    self.backend.cursor = (352, 581)      # mouse left on the button
            return answer

        cal.ask = ask
        cal.calibrate(self.path)
        self.assertIn("covers that button", self.io.text)
        self.assertIn("mouse is over", self.io.text)
        self.assertEqual(len(asked), 3)
        self.assertEqual(load_config(self.path).businesses[0].upgrade_ready_color, GREY)

    def test_recolour_keeps_existing_samples(self):
        self.queue_clicks((500, 300), (150, 560), (350, 580))
        self.backend.colors[(350, 580)] = GREEN
        self.calibrator(["y", "", "Oil", "", "y", "n", "y"]).calibrate(self.path)
        config = load_config(self.path)

        self.backend.colors[(350, 580)] = GREY
        self.calibrator(["n", "n"]).calibrate_colors(config)
        self.assertEqual(config.businesses[0].upgrade_ready_color, GREEN)
        self.assertEqual(config.businesses[0].upgrade_disabled_color, GREY)

    def test_lock_colours(self):
        self.queue_clicks((500, 300), (150, 560), (350, 580))
        self.backend.colors[(150, 560)] = (10, 10, 10)
        # sample colours: y; any locked: y; upgrade lit: skip; unlocked: n
        self.calibrator(["y", "", "Oil", "", "y", "y", "", "n"]).calibrate(self.path)
        oil = load_config(self.path).businesses[0]
        self.assertEqual(oil.tile_locked_color, (10, 10, 10))
        self.assertIsNone(oil.upgrade_ready_color)

    def test_recolour_needs_the_game_running(self):
        self.queue_clicks((500, 300), (150, 560), (350, 580))
        self.calibrator(["y", "", "Oil", "", "n"]).calibrate(self.path)
        config = load_config(self.path)
        del self.backend.windows[GAME]
        with self.assertRaisesRegex(CalibrationError, "Start the game"):
            self.calibrator([]).calibrate_colors(config)


if __name__ == "__main__":
    unittest.main()
