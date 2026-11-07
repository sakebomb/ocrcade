"""Line cropping in the Hailo OCR backend: a crop must read left to right whatever angle
OpenCV reports, and an upside-down page must stay upside down so the rotation search
can reject it. Needs cv2 + numpy (skipped in the lightweight CI job); no Hailo device."""
import pytest

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

from vision.backends.hailo_ocr import (  # noqa: E402
    _class_probs,
    _line_axis,
    _upright,
    readable_lines,
    text_lines,
)


def test_text_lines_finds_nothing_in_an_empty_heatmap():
    _, lines = text_lines(np.zeros((544, 960), np.float32))
    assert lines == []


def test_text_lines_counts_each_line_of_text():
    heat = np.zeros((544, 960), np.float32)
    for top in (100, 160, 220):                      # three lines of "letters"
        for x in range(100, 600, 30):
            heat[top:top + 20, x:x + 20] = 0.9
    heat[400:420, 700:900] = 0.35                    # faint glow, not a line
    _, lines = text_lines(heat)
    assert len(lines) == 3


def test_small_text_in_the_scene_is_not_code_in_view():
    # Measured on kepler: a hotel room gave two text-like blobs at most 34 of 725 px
    # wide; code frames' widest line was always 156 px or more.
    heat = np.zeros((544, 960), np.float32)
    heat[300:310, 100:134] = 0.9                     # 34 px: a shirt print, a sign
    heat[200:212, 500:517] = 0.9
    assert readable_lines(heat, content_w=725) == 0
    heat[100:120, 100:260] = 0.9                     # 160 px: a line of code
    assert readable_lines(heat, content_w=725) == 1


def test_class_probs_softmaxes_logits_over_every_class():
    logits = np.array([[0.0, 2.0, 2.0], [10.0, 0.0, 0.0]])
    p = _class_probs(logits)
    assert np.allclose(p.sum(axis=1), 1.0)
    assert np.isclose(p[0, 1], p[0, 2]) and p[0, 1] < 0.5  # a tie is not confident
    assert p[1, 0] > 0.99


def test_class_probs_leaves_probabilities_alone():
    probs = np.array([[0.1, 0.7, 0.2], [0.5, 0.25, 0.25]])
    assert np.array_equal(_class_probs(probs), probs)


def _line_image(tilt_deg: float) -> np.ndarray:
    """A white page with one text-like bar: black left half, grey right half."""
    img = np.full((400, 400), 255, np.uint8)
    img[190:210, 100:200] = 0
    img[190:210, 200:300] = 128
    m = cv2.getRotationMatrix2D((200, 200), tilt_deg, 1.0)
    return cv2.warpAffine(img, m, (400, 400), flags=cv2.INTER_NEAREST, borderValue=255)


def _crop_halves(img: np.ndarray) -> tuple[float, float]:
    pts = cv2.findNonZero((img < 200).astype(np.uint8))
    rect = cv2.minAreaRect(pts)
    size, angle, _ = _line_axis(rect)
    crop = _upright(img, rect[0], size, angle)
    assert crop.shape[1] > crop.shape[0], "crop should be wide, not tall"
    half = crop.shape[1] // 2
    return float(crop[:, :half].mean()), float(crop[:, half:].mean())


@pytest.mark.parametrize("tilt", [0, 10, -10, 45, -45, 80, -80])
def test_upright_crop_reads_left_to_right_at_any_tilt(tilt):
    left, right = _crop_halves(_line_image(tilt))
    assert left < right  # the black half stays on the left


@pytest.mark.parametrize("tilt", [0, 10, -10])
def test_upside_down_page_stays_upside_down(tilt):
    img = cv2.rotate(_line_image(tilt), cv2.ROTATE_180)
    left, right = _crop_halves(img)
    assert left > right  # not silently flipped back; rotation search must do that
