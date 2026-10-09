"""Deciding a button's state from its colour.

Calibration records what a spot looks like in its "on" state (upgrade
affordable / business unlocked) and/or its "off" state (greyed out / locked).
At run time we compare the live colour against whichever references exist.
"""

from __future__ import annotations

import math
from typing import Optional

from .backend import RGB


def distance(a: RGB, b: RGB) -> float:
    return math.dist(a, b)


def looks_on(sample: RGB, on: Optional[RGB], off: Optional[RGB],
             tolerance: float, default: bool) -> bool:
    """Classify ``sample`` as on/off.

    * both references known: whichever is closer wins
    * only "on" known: on if within ``tolerance`` of it
    * only "off" known: on if further than ``tolerance`` from it
    * neither known: ``default``
    """
    if on is not None and off is not None:
        return distance(sample, on) < distance(sample, off)
    if on is not None:
        return distance(sample, on) <= tolerance
    if off is not None:
        return distance(sample, off) > tolerance
    return default
