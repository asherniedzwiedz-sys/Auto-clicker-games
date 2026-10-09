"""Auto-clicker for idle clicker games (AdVenture Capitalist style).

    python clicker.py --calibrate      one-time set-up
    python clicker.py --dry-run -v     test without clicking
    python clicker.py                  run (F8 pause/resume, F9 quit)

See README.md for details.
"""

import sys

from idleclicker.cli import main

if __name__ == "__main__":
    sys.exit(main())
