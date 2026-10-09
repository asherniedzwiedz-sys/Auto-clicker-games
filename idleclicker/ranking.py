"""Which business counts as "most profitable".

The bot asks its ranker for the businesses best-first, then works on the first
one that is enabled and (if lock colours were sampled) looks unlocked.
"""

from __future__ import annotations

from typing import List, Sequence

from .config import Business


class PriorityRanker:
    """The order the user put the businesses in, in config.json."""

    description = "priority list in config.json"

    def order(self, businesses: Sequence[Business]) -> List[Business]:
        return list(businesses)
