"""Every frame sent to a code reader is saved with its read, for training data."""
import json
import time

import cv2
import numpy as np
from test_pipeline import FakeMarker, FakeReader, Harness, _readable

import daemon
from vision.captures import JOURNAL, CaptureLog
from vision.schema import CardReading

CLOCK = lambda: time.struct_time((2026, 11, 7, 14, 30, 5, 5, 311, 0))  # noqa: E731
FRAME = np.full((120, 160, 3), 200, np.uint8)


def _records(day):
    return [json.loads(line) for line in (day / JOURNAL).read_text().splitlines()]


def test_a_read_saves_its_frame_and_what_was_read(tmp_path):
    log = CaptureLog(tmp_path, min_free_mb=0, clock=CLOCK)
    reading = CardReading(readable=True, code='print("DDRR")', backend="litellm",
                          latency_s=2.345)
    image = log.save(FRAME, reading, 3, "python", "ocrcade")
    assert image == "2026-11-07/143005-p3-1.jpg"
    assert cv2.imread(str(tmp_path / image)).shape == FRAME.shape
    assert _records(tmp_path / "2026-11-07") == [{
        "image": "143005-p3-1.jpg", "time": "2026-11-07T14:30:05", "puzzle": 3,
        "lang": "python", "backend": "litellm", "latency_s": 2.35, "readable": True,
        "reason": "", "team": "ocrcade", "code": 'print("DDRR")'}]


def test_two_reads_in_one_second_never_overwrite(tmp_path):
    log = CaptureLog(tmp_path, min_free_mb=0, clock=CLOCK)
    first = log.save(FRAME, _readable("a"), 3, "python", None)
    second = log.save(FRAME, _readable("b"), 3, "python", None)
    assert first != second and second.endswith("-p3-2.jpg")


def test_a_submitted_read_is_marked(tmp_path):
    log = CaptureLog(tmp_path, min_free_mb=0, clock=CLOCK)
    image = log.save(FRAME, _readable("x"), 3, "python", "t")
    log.mark_submitted(image)
    assert _records(tmp_path / "2026-11-07")[-1] == {"image": "143005-p3-1.jpg",
                                                     "event": "submitted"}


def test_a_nearly_full_disk_stops_saving(tmp_path):
    log = CaptureLog(tmp_path, min_free_mb=10**12, clock=CLOCK)
    assert log.save(FRAME, _readable("x"), 3, "python", None) is None
    assert list(tmp_path.iterdir()) == []


def test_a_write_error_never_breaks_the_read(tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("")
    log = CaptureLog(blocker, min_free_mb=0, clock=CLOCK)
    assert log.save(FRAME, _readable("x"), 3, "python", None) is None


class FakeCaptures:
    def __init__(self):
        self.saved, self.submitted = [], []

    def save(self, image, reading, puzzle, lang, team):
        self.saved.append((image, reading.readable, puzzle, lang, team))
        return f"img{len(self.saved)}"

    def mark_submitted(self, image):
        self.submitted.append(image)


def _run(reading, text_in_view=True):
    captures = FakeCaptures()
    h = Harness(FakeReader(reading, text_in_view=text_in_view), captures=captures)
    h.select(FakeMarker(4, 3, "python"))
    h.show_code()
    h.tick()                                              # confirm 'go' if in readback
    return captures


def test_every_read_is_saved_whatever_its_outcome():
    ok = _run(_readable("# team: Anael\nprint(1)"))
    assert ok.saved == [("M", True, 3, "python", "Anael")] and ok.submitted == ["img1"]
    unread = _run(CardReading.unreadable("blurry", "fake"))
    assert unread.saved == [("M", False, 3, "python", None)] and unread.submitted == []
    stranger = _run(_readable("# team: nobody_here\nprint(1)"))
    assert stranger.saved == [("M", True, 3, "python", None)] and stranger.submitted == []


def test_a_frame_with_no_text_is_not_sent_or_saved():
    assert _run(_readable("x"), text_in_view=False).saved == []


def test_captures_are_off_unless_enabled(tmp_path):
    assert daemon._capture_log({}) is None
    assert daemon._capture_log({"enabled": True, "dir": str(tmp_path)}).root == tmp_path
