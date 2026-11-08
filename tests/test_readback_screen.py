"""The read-back stays up long enough to check, with a countdown to submit."""
import threading
from pathlib import Path

import yaml
from PIL import Image, ImageDraw

import daemon
from display import display_manager as dm

ROOT = Path(__file__).resolve().parents[1]


def _display() -> dm.Display:
    d = dm.Display.__new__(dm.Display)
    d._lock = threading.Lock()
    d._state, d._state_data, d._state_entered, d._last_preview = "attract", {}, 0.0, 0.0
    return d


def test_the_read_back_shows_for_15_seconds():
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    assert cfg["readback"]["hold_s"] == 15.0
    assert cfg["readback"]["timeout_s"] > cfg["readback"]["hold_s"]


def test_the_daemon_sends_its_countdown_with_the_read_back(monkeypatch):
    sent = []
    monkeypatch.setattr(daemon, "_post", lambda url, payload, timeout=5.0: sent.append(payload))
    daemon.ServerUI("http://exec", readback_s=15.0).readback(3, "python", "t", "print(1)")
    assert sent[0]["source_type"] == "readback" and sent[0]["max_stability"] == 15.0


def test_the_countdown_starts_when_the_read_back_arrives(monkeypatch):
    d = _display()
    now = [100.0]
    monkeypatch.setattr(dm.time, "time", lambda: now[0])
    d.preview(["READY"], 0.0, 1.0, "ready_code")             # scanning began earlier
    now[0] = 105.0
    d.preview(["Puzzle 3", "READ:"], 0.0, 15.0, "readback")
    now[0] = 106.0
    d.preview(["Puzzle 3", "READ:"], 0.0, 15.0, "readback")  # a repeat keeps the start
    assert d._state_data["since"] == 105.0


def test_the_read_back_outlasts_the_scanning_timeout():
    data = {"since": 0.0, "max_stability": 15.0}
    assert not dm._readback_over(data, dm.SCANNING_TIMEOUT + 1)
    assert not dm._readback_over(data, 15.0 + dm.READBACK_GRACE_S - 0.1)
    assert dm._readback_over(data, 15.0 + dm.READBACK_GRACE_S + 0.1)


def _bar_end(waited: float) -> int:
    """How far right the countdown bar is filled, in pixels."""
    img = Image.new("RGB", (dm.WIDTH, dm.HEIGHT), dm.DARK)
    data = {"ocr_lines": ["Puzzle 3 · python · team t", "READ:", "print(1)"],
            "max_stability": 15.0}
    _display()._draw_readback(ImageDraw.Draw(img), data, waited)
    y = dm.HEIGHT - dm._s(110) + dm._s(14)
    filled = [x for x in range(62, dm.WIDTH - 62) if img.getpixel((x, y)) == dm.AMBER]
    return max(filled, default=0)


def test_the_countdown_bar_drains_as_time_passes():
    full, half, gone = _bar_end(0.0), _bar_end(7.5), _bar_end(15.0)
    assert full > half > gone
    assert abs((half - 60) / (full - 60) - 0.5) < 0.02
