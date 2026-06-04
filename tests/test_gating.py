"""Regression tests for the frame gate.

The cards are LIGHT text on a DARK screen, so the Otsu content fraction runs high
(~0.8). A prior `max_content=0.6` ceiling silently rejected ~75% of real cards at
the gate. These tests pin the corrected behaviour: blur is the card detector, and
content is only a loose sanity bracket that must NOT reject a normal card.
"""
import pytest

# Vision deps (numpy/cv2) aren't installed in the lightweight CI env; skip there.
np = pytest.importorskip("numpy")
pytest.importorskip("cv2")

from vision.gating import FrameGate  # noqa: E402  (after importorskip guard)


def _card_frame() -> np.ndarray:
    """Dark background + sharp bright 'text' strokes: high edge density (sharp)
    and high Otsu dark-fraction (~0.8), like a real light-on-dark card."""
    f = np.full((480, 640, 3), 20, dtype=np.uint8)  # dark screen
    for y in range(40, 440, 16):                     # bright monospace-ish rows
        f[y:y + 4, 40:600] = 220
    return f


def _blank_bright_frame() -> np.ndarray:
    """Uniform bright screen: ~no edges (blur ~0) and ~no dark pixels."""
    return np.full((480, 640, 3), 240, dtype=np.uint8)


def test_card_fires_after_stable_frames():
    gate = FrameGate()  # defaults: blur_min=60, stable_frames=4, max_content=0.97
    card = _card_frame()
    # content of a real-style card is high; must fall inside the bracket
    assert gate.min_content <= gate.content_fraction(gate._gray(card)) <= gate.max_content
    fired = any(gate.update(card) for _ in range(8))
    assert fired, "a sharp, stable light-on-dark card must trigger a read"


def test_high_content_card_not_rejected_by_ceiling():
    """The exact regression: content ~0.8 must NOT be gated out."""
    gate = FrameGate()
    content = gate.content_fraction(gate._gray(_card_frame()))
    assert content > 0.6, "synthetic card should reproduce the high-content case"
    assert content <= gate.max_content, "raised ceiling must admit a normal card"


def test_blank_frame_never_fires():
    gate = FrameGate()
    blank = _blank_bright_frame()
    assert not any(gate.update(blank) for _ in range(8)), \
        "a low-edge blank frame must never trigger a read"
