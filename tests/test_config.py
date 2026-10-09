import json
import logging
import tempfile
import unittest
from pathlib import Path

from idleclicker.backend import Rect
from idleclicker.colors import looks_on
from idleclicker.config import (Business, Config, ConfigError, box_to_screen, config_from_dict,
                                config_to_dict, load_config, save_config, to_screen)

ROOT = Path(__file__).resolve().parent.parent


def minimal(**overrides):
    data = {
        "window_title": "Example Clicker",
        "client_size": [800, 600],
        "businesses": [{"name": "Oil", "tile": [10, 20], "upgrade": [30, 40]}],
    }
    data.update(overrides)
    return data


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_defaults(self):
        config = config_from_dict(minimal())
        self.assertEqual(config.clicks_per_second, 9.0)
        self.assertEqual(config.pause_key, "F8")
        self.assertTrue(config.businesses[0].enabled)
        self.assertIsNone(config.businesses[0].upgrade_ready_color)

    def test_round_trip(self):
        config = Config(
            window_title="Example Clicker", window_class="UnityWndClass", client_size=(1280, 720),
            businesses=[Business("Oil", (1, 2), (3, 4), enabled=False,
                                 upgrade_ready_color=(1, 2, 3), tile_locked_color=(9, 9, 9))],
            clicks_per_second=8.5, pause_key="F6")
        path = self.dir / "config.json"
        self.assertIsNone(save_config(config, path))
        self.assertEqual(load_config(path), config)

    def test_save_keeps_backup(self):
        path = self.dir / "config.json"
        save_config(config_from_dict(minimal()), path)
        backup = save_config(config_from_dict(minimal(window_title="New")), path)
        self.assertEqual(json.loads(backup.read_text())["window_title"], "Example Clicker")
        self.assertEqual(load_config(path).window_title, "New")

    def test_missing_file_mentions_calibrate(self):
        with self.assertRaisesRegex(ConfigError, "--calibrate"):
            load_config(self.dir / "nope.json")

    def test_invalid_json_reports_line(self):
        path = self.dir / "config.json"
        path.write_text('{\n  "window_title": "x",\n  oops\n}')
        with self.assertRaisesRegex(ConfigError, "line 3"):
            load_config(path)

    def test_rejects_bad_values(self):
        bad = [
            minimal(window_title=""),
            minimal(client_size=[800]),
            minimal(clicks_per_second=0),
            minimal(clicks_per_second=100),
            minimal(title_match="regex"),
            minimal(pause_key="F99"),
            minimal(pause_key="F9", quit_key="f9"),
            minimal(sample_radius=-1),
            minimal(businesses=[{"name": "Oil", "tile": [1, 2]}]),
            minimal(businesses=[{"name": "Oil", "tile": [1, -2], "upgrade": [3, 4]}]),
            minimal(businesses=[{"name": "Oil", "tile": [900, 2], "upgrade": [3, 4]}]),
            minimal(businesses=[{"name": "Oil", "tile": [1, 2], "upgrade": [3, 4],
                                 "upgrade_ready_color": [1, 2, 300]}]),
            minimal(businesses=[{"name": "Oil", "tile": [1, 2], "upgrade": [3, 4], "enabled": "yes"}]),
        ]
        for data in bad:
            with self.subTest(data=data), self.assertRaises(ConfigError):
                config_from_dict(data)

    def test_number_box_and_threshold(self):
        oil = {"name": "Oil", "tile": [10, 20], "upgrade": [30, 40], "number_box": [25, 30, 45, 38]}
        config = config_from_dict(minimal(businesses=[oil], upgrade_at_plus=7))
        self.assertEqual(config.businesses[0].number_box, (25, 30, 45, 38))
        self.assertEqual(config.upgrade_at_plus, 7)
        self.assertEqual(config_from_dict(config_to_dict(config)), config)
        self.assertEqual(config_from_dict(minimal()).upgrade_at_plus, 5)
        for bad_box in ([25, 30, 45], [45, 30, 25, 38], [25, 30, 900, 38], [1, 2, 3, "4"]):
            with self.subTest(box=bad_box), self.assertRaises(ConfigError):
                config_from_dict(minimal(businesses=[dict(oil, number_box=bad_box)]))
        for bad in (0, -1, 2.5, "5", True):
            with self.subTest(upgrade_at_plus=bad), self.assertRaises(ConfigError):
                config_from_dict(minimal(upgrade_at_plus=bad))

    def test_key_names_are_case_insensitive(self):
        self.assertEqual(config_from_dict(minimal(pause_key="f6")).pause_key, "F6")

    def test_warns_about_unknown_keys_but_not_notes(self):
        with self.assertLogs("idleclicker.config", logging.WARNING) as logs:
            config_from_dict(minimal(clicks_per_secnd=5, _note="hi"))
        self.assertEqual(len(logs.output), 1)
        self.assertIn("clicks_per_secnd", logs.output[0])

    def test_saved_file_has_priority_note(self):
        data = config_to_dict(config_from_dict(minimal()))
        self.assertIn("FIRST", data["_businesses"])

    def test_example_config_is_valid(self):
        config = load_config(ROOT / "config.example.json")
        self.assertTrue(config.businesses)


class ToScreenTests(unittest.TestCase):
    def test_offset_from_client_origin(self):
        self.assertEqual(to_screen((10, 20), (800, 600), Rect(100, 50, 800, 600)), (110, 70))

    def test_scales_with_window_size(self):
        self.assertEqual(to_screen((400, 300), (800, 600), Rect(0, 0, 1600, 1200)), (800, 600))

    def test_negative_window_position_on_left_monitor(self):
        self.assertEqual(to_screen((10, 20), (800, 600), Rect(-1920, 0, 800, 600)), (-1910, 20))

    def test_clamped_inside_client_area(self):
        self.assertEqual(to_screen((799, 599), (800, 600), Rect(0, 0, 400, 300)), (399, 299))

    def test_box_padding_stays_inside_client_area(self):
        client = Rect(100, 50, 800, 600)
        self.assertEqual(box_to_screen((10, 20, 30, 40), (800, 600), client, 4), (106, 66, 134, 94))
        self.assertEqual(box_to_screen((0, 0, 799, 599), (800, 600), client, 4), (100, 50, 899, 649))


class ColorTests(unittest.TestCase):
    def test_both_references_nearest_wins(self):
        self.assertTrue(looks_on((30, 190, 50), (40, 200, 60), (128, 128, 128), 40, False))
        self.assertFalse(looks_on((120, 130, 125), (40, 200, 60), (128, 128, 128), 40, True))

    def test_only_on_reference_uses_tolerance(self):
        self.assertTrue(looks_on((45, 195, 60), (40, 200, 60), None, 40, False))
        self.assertFalse(looks_on((128, 128, 128), (40, 200, 60), None, 40, True))

    def test_only_off_reference_uses_tolerance(self):
        self.assertFalse(looks_on((125, 125, 130), None, (128, 128, 128), 40, True))
        self.assertTrue(looks_on((40, 200, 60), None, (128, 128, 128), 40, False))

    def test_no_references_returns_default(self):
        self.assertTrue(looks_on((0, 0, 0), None, None, 40, True))
        self.assertFalse(looks_on((0, 0, 0), None, None, 40, False))


if __name__ == "__main__":
    unittest.main()
