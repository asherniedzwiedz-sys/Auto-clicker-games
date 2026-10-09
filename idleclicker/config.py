"""config.json: the game window, the ranked businesses, and tuning knobs.

Business coordinates are pixel offsets from the top-left corner of the game
window's client area (the part inside the borders/title bar), measured while
the client area was ``client_size``. Moving the window doesn't affect them; if
the window is resized they are scaled proportionally.

The order of ``businesses`` is the priority: put the highest earner first.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .backend import KEY_CODES, RGB, Point, Rect

log = logging.getLogger(__name__)


class ConfigError(ValueError):
    pass


@dataclass
class Business:
    name: str
    tile: Point
    upgrade: Point
    enabled: bool = True
    upgrade_ready_color: Optional[RGB] = None
    upgrade_disabled_color: Optional[RGB] = None
    tile_active_color: Optional[RGB] = None
    tile_locked_color: Optional[RGB] = None

    @property
    def has_upgrade_colors(self) -> bool:
        return self.upgrade_ready_color is not None or self.upgrade_disabled_color is not None

    @property
    def has_tile_colors(self) -> bool:
        return self.tile_active_color is not None or self.tile_locked_color is not None


@dataclass
class Config:
    window_title: str
    client_size: Tuple[int, int]
    businesses: List[Business] = field(default_factory=list)
    window_class: str = ""
    title_match: str = "contains"
    clicks_per_second: float = 9.0
    click_hold_ms: int = 20
    upgrade_check_seconds: float = 3.0
    color_tolerance: float = 40.0
    sample_radius: int = 3
    user_pause_seconds: float = 3.0
    pause_key: str = "F8"
    quit_key: str = "F9"


def to_screen(offset: Point, client_size: Tuple[int, int], client: Rect) -> Point:
    """Turn a calibrated offset into a screen point for the window's current position/size."""
    cal_w, cal_h = client_size
    x = round(offset[0] * client.width / cal_w)
    y = round(offset[1] * client.height / cal_h)
    x = min(max(x, 0), client.width - 1)
    y = min(max(y, 0), client.height - 1)
    return client.left + x, client.top + y


# ---------------------------------------------------------------- loading

def _point(value: Any, where: str) -> Point:
    if (not isinstance(value, (list, tuple)) or len(value) != 2
            or not all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in value)):
        raise ConfigError(f"{where} must be [x, y] with two whole numbers >= 0, got {value!r}")
    return int(value[0]), int(value[1])


def _color(value: Any, where: str) -> Optional[RGB]:
    if value is None:
        return None
    if (not isinstance(value, (list, tuple)) or len(value) != 3
            or not all(isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 255 for v in value)):
        raise ConfigError(f"{where} must be [r, g, b] (0-255 each) or null, got {value!r}")
    return int(value[0]), int(value[1]), int(value[2])


def _number(data: Dict[str, Any], key: str, default: float, lo: float, hi: float) -> float:
    value = data.get(key, default)
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not lo <= value <= hi:
        raise ConfigError(f"{key} must be a number from {lo} to {hi}, got {value!r}")
    return value


def _warn_unknown(data: Dict[str, Any], known: set, where: str) -> None:
    # Keys starting with "_" are free for notes/comments.
    for key in data:
        if key not in known and not key.startswith("_"):
            log.warning("Ignoring unknown setting %r in %s (typo?)", key, where)


def _business(data: Any, index: int) -> Business:
    where = f"businesses[{index}]"
    if not isinstance(data, dict):
        raise ConfigError(f"{where} must be an object")
    _warn_unknown(data, {f.name for f in fields(Business)}, where)
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ConfigError(f"{where}.name must be a non-empty string")
    where = f"business {name!r}"
    for key in ("tile", "upgrade"):
        if key not in data:
            raise ConfigError(f"{where} is missing {key!r} (run --calibrate)")
    enabled = data.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ConfigError(f"{where}.enabled must be true or false")
    return Business(
        name=name,
        tile=_point(data["tile"], f"{where}.tile"),
        upgrade=_point(data["upgrade"], f"{where}.upgrade"),
        enabled=enabled,
        upgrade_ready_color=_color(data.get("upgrade_ready_color"), f"{where}.upgrade_ready_color"),
        upgrade_disabled_color=_color(data.get("upgrade_disabled_color"), f"{where}.upgrade_disabled_color"),
        tile_active_color=_color(data.get("tile_active_color"), f"{where}.tile_active_color"),
        tile_locked_color=_color(data.get("tile_locked_color"), f"{where}.tile_locked_color"),
    )


def config_from_dict(data: Any) -> Config:
    if not isinstance(data, dict):
        raise ConfigError("config.json must contain a JSON object")
    _warn_unknown(data, {f.name for f in fields(Config)}, "config.json")

    title = data.get("window_title")
    if not isinstance(title, str) or not title.strip():
        raise ConfigError("window_title must be a non-empty string (run --calibrate)")
    window_class = data.get("window_class", "")
    if not isinstance(window_class, str):
        raise ConfigError("window_class must be a string (use \"\" to match any)")
    title_match = data.get("title_match", "contains")
    if title_match not in ("contains", "exact"):
        raise ConfigError('title_match must be "contains" or "exact"')

    size = data.get("client_size")
    if (not isinstance(size, (list, tuple)) or len(size) != 2
            or not all(isinstance(v, int) and not isinstance(v, bool) and v > 0 for v in size)):
        raise ConfigError(f"client_size must be [width, height] (run --calibrate), got {size!r}")

    raw_businesses = data.get("businesses", [])
    if not isinstance(raw_businesses, list):
        raise ConfigError("businesses must be a list")
    businesses = [_business(b, i) for i, b in enumerate(raw_businesses)]
    for b in businesses:
        for key in ("tile", "upgrade"):
            x, y = getattr(b, key)
            if x >= size[0] or y >= size[1]:
                raise ConfigError(f"business {b.name!r} {key} {[x, y]} is outside the "
                                  f"calibrated window size {list(size)}")

    keys = {}
    for key, default in (("pause_key", "F8"), ("quit_key", "F9")):
        value = data.get(key, default)
        if not isinstance(value, str) or value.upper() not in KEY_CODES:
            raise ConfigError(f"{key} must be a key name like \"F8\", got {value!r}")
        keys[key] = value.upper()
    if keys["pause_key"] == keys["quit_key"]:
        raise ConfigError("pause_key and quit_key must be different keys")

    radius = data.get("sample_radius", 3)
    if not isinstance(radius, int) or isinstance(radius, bool) or not 0 <= radius <= 10:
        raise ConfigError(f"sample_radius must be a whole number from 0 to 10, got {radius!r}")

    return Config(
        window_title=title,
        client_size=(size[0], size[1]),
        businesses=businesses,
        window_class=window_class,
        title_match=title_match,
        clicks_per_second=_number(data, "clicks_per_second", 9.0, 0.5, 30),
        click_hold_ms=int(_number(data, "click_hold_ms", 20, 0, 200)),
        upgrade_check_seconds=_number(data, "upgrade_check_seconds", 3.0, 0.5, 3600),
        color_tolerance=_number(data, "color_tolerance", 40.0, 1, 442),
        sample_radius=radius,
        user_pause_seconds=_number(data, "user_pause_seconds", 3.0, 0, 60),
        pause_key=keys["pause_key"],
        quit_key=keys["quit_key"],
    )


def load_config(path: Path) -> Config:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigError(f"{path} not found. Run with --calibrate first.") from None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ConfigError(f"{path} is not valid JSON (line {e.lineno}, column {e.colno}): {e.msg}") from None
    return config_from_dict(data)


# ---------------------------------------------------------------- saving

def config_to_dict(config: Config) -> Dict[str, Any]:
    def business(b: Business) -> Dict[str, Any]:
        return {
            "name": b.name,
            "enabled": b.enabled,
            "tile": list(b.tile),
            "upgrade": list(b.upgrade),
            "upgrade_ready_color": list(b.upgrade_ready_color) if b.upgrade_ready_color else None,
            "upgrade_disabled_color": list(b.upgrade_disabled_color) if b.upgrade_disabled_color else None,
            "tile_active_color": list(b.tile_active_color) if b.tile_active_color else None,
            "tile_locked_color": list(b.tile_locked_color) if b.tile_locked_color else None,
        }

    return {
        "window_title": config.window_title,
        "window_class": config.window_class,
        "title_match": config.title_match,
        "client_size": list(config.client_size),
        "clicks_per_second": config.clicks_per_second,
        "click_hold_ms": config.click_hold_ms,
        "upgrade_check_seconds": config.upgrade_check_seconds,
        "color_tolerance": config.color_tolerance,
        "sample_radius": config.sample_radius,
        "user_pause_seconds": config.user_pause_seconds,
        "pause_key": config.pause_key,
        "quit_key": config.quit_key,
        "_businesses": "Highest earner FIRST. The bot works on the first enabled, unlocked one.",
        "businesses": [business(b) for b in config.businesses],
    }


def save_config(config: Config, path: Path) -> Optional[Path]:
    """Write config.json, keeping the previous file as config.json.bak. Returns the backup path."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(config_to_dict(config), indent=2) + "\n", encoding="utf-8")
    backup = None
    if path.exists():
        backup = path.with_name(path.name + ".bak")
        shutil.copy2(path, backup)
    os.replace(tmp, path)
    return backup
