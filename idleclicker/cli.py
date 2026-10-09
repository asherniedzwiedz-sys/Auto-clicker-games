"""Command line: python clicker.py [--calibrate | --calibrate-colors] [--dry-run] [--ocr] [-v]"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import List, Optional

from .backend import UnsupportedPlatformError, create_backend
from .bot import Bot
from .calibrate import CalibrationError, Calibrator
from .config import ConfigError, load_config, save_config
from .ocr import OcrRanker
from .ranking import PriorityRanker

log = logging.getLogger("idleclicker")


def parse_args(argv: Optional[List[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="clicker.py",
        description="Auto-clicker for idle clicker games: taps your top business and buys its "
                    "upgrades, only while the game is the active window.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--calibrate", action="store_true",
                      help="walk through clicking each business tile and upgrade button, "
                           "and save their positions to the config file")
    mode.add_argument("--calibrate-colors", action="store_true",
                      help="only re-sample the upgrade/tile colours (keeps positions)")
    parser.add_argument("--config", type=Path, default=Path("config.json"),
                        help="config file to use (default: config.json)")
    parser.add_argument("--dry-run", action="store_true",
                        help="do everything except actually clicking (use with -v to see what it would do)")
    parser.add_argument("--ocr", action="store_true",
                        help="rank businesses by reading their income off the screen "
                             "(NOT IMPLEMENTED YET: falls back to the config order)")
    parser.add_argument("-v", "--verbose", action="store_true", help="log every decision")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
    try:
        backend = create_backend()
    except UnsupportedPlatformError as e:
        print(e, file=sys.stderr)
        return 2

    try:
        if args.calibrate:
            Calibrator(backend).calibrate(args.config)
            return 0

        config = load_config(args.config)
        if args.calibrate_colors:
            Calibrator(backend).calibrate_colors(config)
            save_config(config, args.config)
            print(f"Saved {args.config}.")
            return 0

        if not config.businesses:
            raise ConfigError(f"{args.config} has no businesses. Run with --calibrate.")
        if not any(b.enabled for b in config.businesses):
            raise ConfigError(f'Every business in {args.config} has "enabled": false.')

        ranker = PriorityRanker()
        if args.ocr:
            log.warning("--ocr isn't implemented yet; using the priority list in %s.", args.config)
            ranker = OcrRanker(backend, fallback=ranker)
        Bot(config, backend, ranker=ranker, dry_run=args.dry_run).run()
        return 0
    except ConfigError as e:
        print(f"Config problem: {e}", file=sys.stderr)
        return 1
    except CalibrationError as e:
        print(f"Calibration stopped: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nStopped." if not (args.calibrate or args.calibrate_colors)
              else "\nCancelled; config not changed.")
        return 130
