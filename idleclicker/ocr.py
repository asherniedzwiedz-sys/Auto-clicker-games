"""Stub for the --ocr option. NOT IMPLEMENTED YET.

The plan: read each business's income off the screen and rank the businesses
by it, so the priority list in config.json doesn't need maintaining by hand.
Doing that needs an OCR engine (e.g. Tesseract via pytesseract) plus a
calibrated screen region per business for its income text.

Until then OcrRanker just falls back to the given ranker, so --ocr is safe to
pass. To implement it, fill in ``read_incomes`` and keep the fallback for any
business whose income couldn't be read.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Sequence

from .backend import Backend
from .config import Business

log = logging.getLogger(__name__)


class OcrRanker:
    description = "OCR (not implemented yet; using the priority list in config.json)"

    def __init__(self, backend: Backend, fallback) -> None:
        self.backend = backend
        self.fallback = fallback

    def read_incomes(self, businesses: Sequence[Business]) -> Optional[Dict[str, float]]:
        """Income per second for each business name, or None if unavailable."""
        return None

    def order(self, businesses: Sequence[Business]) -> List[Business]:
        incomes = self.read_incomes(businesses)
        if not incomes:
            return self.fallback.order(businesses)
        return sorted(businesses, key=lambda b: incomes.get(b.name, float("-inf")), reverse=True)
