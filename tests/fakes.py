from collections import deque

from idleclicker.backend import Rect, WindowInfo

GAME = 1
OTHER = 2
GREY = (128, 128, 128)


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class FakeBackend:
    """A desktop with the game window (handle 1) and one other window (handle 2)."""

    def __init__(self, clock=None):
        self.clock = clock
        self.windows = {
            GAME: WindowInfo(GAME, "Example Clicker", "UnityWndClass"),
            OTHER: WindowInfo(OTHER, "Notepad", "Notepad"),
        }
        self.rects = {GAME: Rect(100, 50, 800, 600), OTHER: Rect(1000, 0, 400, 400)}
        self.foreground = GAME
        self.minimized = set()
        self.covered = set()          # screen points where OTHER sits on top of the game
        self.cursor = (0, 0)
        self.clicks = []              # (x, y) of every click sent
        self.colors = {}              # screen point -> RGB
        self.keys_down = set()
        self.mouse_events = deque()   # scripted (down, pos) for calibration
        self.refuse_clicks = False

    # --- windows
    def list_windows(self):
        return list(self.windows.values())

    def window_info(self, handle):
        return self.windows.get(handle)

    def is_window(self, handle):
        return handle in self.windows

    def is_minimized(self, handle):
        return handle in self.minimized

    def foreground_window(self):
        return self.foreground

    def client_rect(self, handle):
        return self.rects.get(handle)

    def window_at(self, x, y):
        if (x, y) in self.covered:
            return OTHER
        for handle in (GAME, OTHER):
            rect = self.rects.get(handle)
            if handle in self.windows and rect and rect.contains(x, y):
                return handle
        return None

    # --- input
    def cursor_pos(self):
        return self.cursor

    def is_mouse_down(self):
        if not self.mouse_events:
            raise RuntimeError("test ran out of scripted mouse input")
        down, pos = self.mouse_events.popleft()
        self.cursor = pos
        return down

    def queue_click(self, x, y):
        self.mouse_events.extend([(False, (x, y)), (True, (x, y)), (False, (x, y))])

    def is_key_down(self, key):
        return key in self.keys_down

    def click(self, x, y, hold_seconds):
        if self.refuse_clicks:
            return False
        assert self.foreground == GAME, "clicked while the game wasn't in the foreground"
        self.cursor = (x, y)
        self.clicks.append((x, y))
        if self.clock is not None:
            self.clock.sleep(hold_seconds)
        return True

    def sample_color(self, x, y, radius):
        return self.colors.get((x, y), GREY)
