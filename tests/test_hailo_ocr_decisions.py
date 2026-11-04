"""Hailo OCR frame decisions with the device faked: when the upright read is enough,
which rotation wins, and when the reader declines. Needs cv2 + numpy (skips in CI)."""
import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("cv2")

from vision.backends import hailo_ocr  # noqa: E402
from vision.backends.hailo_ocr import HailoOCRBackend, OCRResult  # noqa: E402

FRAME = np.zeros((40, 60, 3), np.uint8)


def backend(reads: dict[int, OCRResult]):
    b = HailoOCRBackend("det", "rec", "keys")
    b._ensure_loaded = lambda: True
    seen = []

    def fake(_rgb, rot):
        seen.append(rot)
        return reads[rot]

    b._read_frame = fake
    return b, seen


def res(rot, chars, conf, text="print(1)"):
    return OCRResult(text, chars, conf, rot)


def test_a_strong_upright_read_skips_the_other_rotations():
    b, seen = backend({0: res(0, 40, 0.95)})
    assert b.read(FRAME).rotation == 0 and seen == [0]


def test_a_weak_upright_read_tries_every_rotation_and_keeps_the_best():
    b, seen = backend({0: res(0, 40, 0.6), 90: res(90, 50, 0.9), 180: res(180, 5, 0.9),
                       270: res(270, 40, 0.7)})
    assert b.read(FRAME).rotation == 90 and seen == [0, 90, 180, 270]


def test_a_short_upright_read_is_not_enough_on_its_own():
    b, seen = backend({r: res(r, 10, 0.99) for r in (0, 90, 180, 270)})
    b.read(FRAME)
    assert seen == [0, 90, 180, 270]


@pytest.mark.parametrize("chars,conf,readable", [
    (40, 0.95, True), (14, 0.99, False), (40, 0.69, False), (15, 0.70, True)])
def test_read_code_declines_short_or_unconfident_reads(chars, conf, readable):
    b, _ = backend({r: res(r, chars, conf) for r in (0, 90, 180, 270)})
    r = b.read_code(FRAME)
    assert r.readable is readable and not r.failed


def test_load_failure_is_a_failure_not_a_decline():
    b = HailoOCRBackend("det", "rec", "keys")
    b._ensure_loaded = lambda: False
    b._load_error = "no device"
    r = b.read_code(FRAME)
    assert r.failed and "no device" in r.reason


def test_thresholds_match_the_documented_tuning():
    assert hailo_ocr.UPRIGHT_ENOUGH == (20, 0.85) and hailo_ocr.ABSTAIN_BELOW == (15, 0.70)
