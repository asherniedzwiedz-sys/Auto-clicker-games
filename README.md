# Auto-clicker-games

Auto clicks and auto upgrades for idle clicker games in the style of
AdVenture Capitalist. It works with any clicker game that has a business tile
and an upgrade/buy button.

While the game is the active window, it:

1. **taps** the business you picked about 9 times a second, and
2. **every 3 seconds** checks whether that business's upgrade button looks
   affordable and, if so, clicks it. It never touches any other business.

It works purely from screen positions and pixel colours, the way a person
does. It doesn't read game memory, inject DLLs or need admin rights, and it
has no dependencies beyond Python's standard library.

## Safety: it never clicks anywhere else

Before **every** click it re-checks that:

- the game window is the **foreground** (active) window, and not minimised;
- the click point is inside the game window, recomputed from the window's
  **current** position on every loop, so moving the window is fine;
- nothing (a popup, a notification, another window) is covering that spot.

If any check fails, it doesn't click. Alt-Tab to something else and it stops
clicking straight away.

It also gives the mouse back to you: **if you move the mouse, it stops
clicking** until the mouse has been still for 3 seconds. The same 3-second
wait applies whenever you switch back to the game.

| Control | What it does |
|---|---|
| **F8** | pause / resume |
| **F9** | quit |
| **Ctrl+C** in the console | quit |
| move the mouse | pause until the mouse is still for 3 s |

## Setup (Windows)

1. Install **Python 3.8 or newer** from <https://www.python.org/downloads/>.
   During install, tick **"Add python.exe to PATH"**.
2. Download this repository (green **Code** button, then **Download ZIP**) and unzip it.
3. Start the game in **windowed** or **borderless windowed** mode, at the size
   you'll play at. Exclusive fullscreen can stop the colour checks from working.

## 1. Calibrate (one time)

Double-click **`Calibrate.bat`**, or run in a terminal in this folder:

```
python clicker.py --calibrate
```

It walks you through it:

1. **Click on the game itself** (not its taskbar button) so it learns which
   window is the game. It saves the window's title and finds the game by
   title from then on. Press Enter to keep the title it found.
2. **Name the business to tap and upgrade.** Pick your best earner. The name
   is only a label for you.
3. **Click that business's tile, then its upgrade button.** Your clicks go
   through to the game normally.
4. **(Recommended) The upgrade button's colour.** Answer whether the upgrade
   button is lit up (affordable) *right now*. If this console is covering the
   button, click the game on the taskbar to bring it to the front and leave
   the mouse alone: it reads the colour by itself as soon as the button is
   visible, then you switch back.

Everything is saved to `config.json`. If one already exists, the old one is
kept as `config.json.bak`.

## 2. Test without clicking

```
python clicker.py --dry-run -v
```

This does everything except click, and logs what it would click and the
colours it sees.

## 3. Run

Double-click **`Start Clicker.bat`**, or run `python clicker.py`. Then click
on the game window and let go of the mouse. Clicking starts 3 seconds later.

## Changing which business it works on

Run **`Calibrate.bat`** again and pick the new business. Your other settings
are kept.

(Advanced: `config.json` can list several businesses, best first. The bot
works on the first one that isn't set to `"enabled": false`.)

## How "affordable" is detected

During colour sampling you record what a button looks like **lit up** and/or
**greyed out**. At run time it compares the button's current colour (averaged
over a 7x7 pixel square) with those samples:

- both samples recorded: whichever is closer wins (most reliable);
- only one recorded: it uses `color_tolerance` to decide;
- none recorded: it just clicks the upgrade button every check. That's
  harmless in most games, which ignore clicks on buttons you can't afford.

To add the other state (for example, sample again when the button is
greyed out), double-click **`Calibrate Colours.bat`**, or run:

```
python clicker.py --calibrate-colors
```

This keeps your positions and any existing samples.

## Settings (`config.json`)

| Setting | Default | Meaning |
|---|---|---|
| `window_title` | from calibration | Text in the game's window title (case-insensitive) |
| `window_class` | from calibration | Window class name; `""` matches any |
| `title_match` | `"contains"` | `"contains"` or `"exact"` |
| `client_size` | from calibration | Window inner size when calibrated; positions scale if the size changes |
| `clicks_per_second` | `9` | Tap rate (0.5–30) |
| `click_hold_ms` | `20` | How long each click holds the button down |
| `upgrade_check_seconds` | `3` | How often to check and buy the upgrade |
| `color_tolerance` | `40` | How close a colour must be to a single sample (0–442) |
| `sample_radius` | `3` | Colour is averaged over a (2r+1)² square |
| `user_pause_seconds` | `3` | Mouse must be still this long before clicking (re)starts |
| `pause_key` / `quit_key` | `F8` / `F9` | Hotkeys (F1–F24, letters, digits, PAUSE, ...) |

Positions are pixel offsets from the top-left corner of the game's inner
area, so they don't depend on where the window is. See `config.example.json`
for a full example.

## `--ocr` (not implemented yet)

`--ocr` is reserved for ranking businesses by reading their income off the
screen. For now it only prints a notice and uses the priority list. The stub
is in `idleclicker/ocr.py`.

## Troubleshooting

- **"Waiting for the game window"**: the title in `config.json` doesn't
  match. Check the game's title bar, or re-run `--calibrate`.
- **Clicks land in the wrong place**: re-run `--calibrate` at the size you
  play at. Resizing the window scales positions, but games whose layout
  changes with aspect ratio need recalibrating. Games with a scrolling
  business list must stay scrolled to the same position.
- **"Windows refused to send the click"**: the game runs as administrator.
  Windows blocks normal programs from clicking into elevated ones. Run the
  game normally instead.
- **It never buys upgrades / always clicks them**: run `--dry-run -v` to see
  the colours it reads, then `--calibrate-colors`. If the lit and greyed
  samples are almost identical, you probably clicked on text. Recalibrate and
  click a plain part of the button.
- **It keeps saying it's waiting for the mouse**: something keeps moving the
  cursor, such as a jittery mouse or touchpad. Set `user_pause_seconds` to `0`
  to switch the hand-off off; F8/F9 still work.

Only use it in single-player/offline games, and check the game's terms of
service first.

## Development

```
python -m unittest discover -s tests -t .
```

The bot logic sits behind a small backend interface (`idleclicker/backend.py`).
The real Windows calls are in `idleclicker/win32_backend.py`. The tests use a
fake desktop, so they run on any OS.
