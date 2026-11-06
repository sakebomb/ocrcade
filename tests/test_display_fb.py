"""Framebuffer open and unblank. No /dev/fb0 required."""
import os

from display import display_manager
from display.display_manager import open_fb, unblank, wake


def test_unblank_writes_zero(tmp_path):
    blank = tmp_path / "blank"
    blank.write_text("4")
    unblank(blank)
    assert blank.read_text() == "0"


def test_unblank_ignores_a_missing_file(tmp_path):
    unblank(tmp_path / "missing")


def test_open_fb_returns_none_when_the_device_is_missing(tmp_path):
    assert open_fb(str(tmp_path / "no-such-dir" / "fb0")) is None


def test_open_fb_does_not_create_a_missing_file(tmp_path):
    missing = tmp_path / "fb0"
    assert open_fb(str(missing)) is None
    assert not missing.exists()


def test_open_fb_opens_an_existing_file(tmp_path):
    dev = tmp_path / "fb0"
    dev.write_bytes(b"")
    fh = open_fb(str(dev))
    assert fh is not None
    assert os.get_inheritable(fh.fileno()) is False
    fh.close()


def test_wake_uses_the_framebuffer_ioctl(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(display_manager.fcntl, "ioctl",
                        lambda fb, req, arg: calls.append((req, arg)))
    blank = tmp_path / "blank"
    blank.write_text("4")
    dev = tmp_path / "fb0"
    dev.write_bytes(b"")
    with open(dev, "wb") as fh:
        wake(fh, blank)
    assert calls == [(display_manager.FBIOBLANK, 0)]
    assert blank.read_text() == "4"


def test_wake_falls_back_to_sysfs_when_the_ioctl_is_refused(monkeypatch, tmp_path):
    def refused(*_a):
        raise OSError("not a framebuffer")

    monkeypatch.setattr(display_manager.fcntl, "ioctl", refused)
    blank = tmp_path / "blank"
    blank.write_text("4")
    dev = tmp_path / "fb0"
    dev.write_bytes(b"")
    with open(dev, "wb") as fh:
        wake(fh, blank)
    assert blank.read_text() == "0"


def _display_without_framebuffer(monkeypatch, opens):
    """A Display whose first open failed at boot, without its render thread."""
    import threading
    clock = {'now': 100.0}
    monkeypatch.setattr(display_manager.time, 'monotonic', lambda: clock['now'])
    monkeypatch.setattr(display_manager, 'open_fb', lambda: opens.pop(0))
    monkeypatch.setattr(display_manager, 'wake', lambda fb: None)
    d = display_manager.Display.__new__(display_manager.Display)
    d._fb, d._fb_paused, d._lock = None, False, threading.Lock()
    d._fb_retry_at = clock['now']
    return d, clock


def test_blit_opens_the_framebuffer_once_it_becomes_available(monkeypatch, tmp_path):
    # Boot race on kepler: the service starts before the HDMI driver gives
    # /dev/fb0 its final permissions, so the first open fails.
    dev = tmp_path / "fb0"
    dev.write_bytes(b"")
    late = open(dev, 'wb')
    d, clock = _display_without_framebuffer(monkeypatch, [None, late])
    frame = display_manager.Image.new('RGB', (display_manager.WIDTH, display_manager.HEIGHT))

    clock['now'] += display_manager.FB_RETRY_S
    d._blit(frame)              # device still refused
    assert d._fb is None
    clock['now'] += display_manager.FB_RETRY_S
    d._blit(frame)              # device ready now

    assert d._fb is late
    late.close()
    assert dev.stat().st_size > 0


def test_blit_waits_between_framebuffer_retries(monkeypatch):
    d, clock = _display_without_framebuffer(monkeypatch, [None])
    frame = display_manager.Image.new('RGB', (display_manager.WIDTH, display_manager.HEIGHT))

    clock['now'] += display_manager.FB_RETRY_S / 2
    d._blit(frame)              # too soon: must not call open_fb (list would be consumed)

    assert d._fb is None
    assert d._fb_retry_at == 100.0
