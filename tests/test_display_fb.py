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
