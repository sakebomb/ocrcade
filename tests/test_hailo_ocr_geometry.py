"""Line cropping in the Hailo OCR backend: a crop must read left to right whatever angle
OpenCV reports, and an upside-down page must stay upside down so the rotation search
can reject it. Needs cv2 + numpy (skipped in the lightweight CI job); no Hailo device."""
import pytest

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

from vision.backends.hailo_ocr import _line_axis, _upright  # noqa: E402


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
