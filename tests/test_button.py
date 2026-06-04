"""ButtonConfirm tests with an injected fake GPIO reader (pure — runs in CI)."""
from hw.button import ButtonConfirm


class FakeGpio:
    """Scriptable level reader: 1 = released, 0 = pressed."""
    def __init__(self, levels):
        self.levels = list(levels)
    def __call__(self):
        return self.levels.pop(0) if self.levels else 1


class Clock:
    def __init__(self):
        self.t = 0.0
    def __call__(self):
        return self.t


def test_press_edge_returns_go():
    gpio = FakeGpio([1, 1, 0])          # released, released, pressed
    b = ButtonConfirm(read_level=gpio, now=Clock())
    b.arm()
    assert b.poll() is None             # still released
    assert b.poll() == "go"             # 1→0 edge


def test_no_go_while_released():
    b = ButtonConfirm(read_level=FakeGpio([1, 1, 1]), now=Clock())
    b.arm()
    assert b.poll() is None
    assert b.poll() is None


def test_held_at_arm_does_not_autofire():
    # button already held (0) when armed: must wait for release then a fresh press
    gpio = FakeGpio([0, 0, 1, 0])       # arm reads 0; held; release; press
    b = ButtonConfirm(read_level=gpio, now=Clock())
    b.arm()                             # snapshots 0
    assert b.poll() is None             # still 0, no 1→0 edge
    assert b.poll() is None             # back to 1 (release)
    assert b.poll() == "go"             # 1→0 fresh press


def test_debounce_suppresses_bounce():
    clock = Clock()
    # press, bounce-release, bounce-press within debounce window → only one go
    gpio = FakeGpio([1, 0, 1, 0])
    b = ButtonConfirm(read_level=gpio, debounce_s=0.05, now=clock)
    b.arm()
    assert b.poll() == "go"             # first edge at t=0
    b.poll()                            # release (no edge)
    assert b.poll() is None             # second 1→0 within 0.05s debounce → suppressed
