from collections import deque

from concurrent.futures import Future

from idleclicker.backend import Image, Rect, WindowInfo

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
        self.ctrl_events = deque()    # scripted Ctrl (down, pos) for calibration
        self.grabs = []               # (left, top, width, height) of every screen capture
        self.moves = []               # (x, y) of every mouse move without a click
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
        for handle, rect in self.rects.items():
            if handle in self.windows and rect.contains(x, y):
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

    def queue_ctrl_press(self, x, y):
        self.ctrl_events.extend([(False, (x, y)), (True, (x, y)), (False, (x, y))])

    def is_key_down(self, key):
        if key == "CTRL" and key not in self.keys_down:
            if not self.ctrl_events:
                raise RuntimeError("test ran out of scripted Ctrl presses")
            down, pos = self.ctrl_events.popleft()
            self.cursor = pos
            return down
        return key in self.keys_down

    def move_cursor(self, x, y):
        self.cursor = (x, y)
        self.moves.append((x, y))

    def grab(self, left, top, width, height):
        self.grabs.append((left, top, width, height))
        return Image(width, height, bytes(width * height * 4))

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


class FakeReader:
    """Stands in for Windows OCR: returns the scripted texts in order (the last one repeats)."""

    def __init__(self, *texts):
        self.texts = list(texts)
        self.images = []
        self.closed = 0

    def read(self, image):
        self.images.append(image)
        text = self.texts.pop(0) if len(self.texts) > 1 else self.texts[0]
        if isinstance(text, Exception):
            raise text
        return text

    def close(self):
        self.closed += 1


class ImmediateExecutor:
    """Runs submitted work straight away, so tests don't depend on thread timing."""

    def submit(self, fn, *args):
        future = Future()
        try:
            future.set_result(fn(*args))
        except Exception as e:
            future.set_exception(e)
        return future

    def shutdown(self, wait=True):
        pass


class NeverFinishesExecutor:
    """Work that never completes, like a stuck OCR engine."""

    def submit(self, fn, *args):
        return Future()

    def shutdown(self, wait=True):
        pass
