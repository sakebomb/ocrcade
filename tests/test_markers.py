"""Tests for the ArUco marker ID -> (puzzle, lang) map (pure — runs in CI).

The cv2-based MarkerDetector is exercised on-device (importorskip-guarded below);
the mapping logic is what we lock here."""
import pytest

from vision.markers import LANGS, default_marker_map, lookup


def test_map_has_18_entries_one_per_puzzle_lang():
    m = default_marker_map()
    assert len(m) == 6 * len(LANGS) == 18
    assert set(m.keys()) == set(range(1, 19))
    # every (puzzle, lang) combo present exactly once
    assert sorted(m.values()) == sorted((p, lg) for p in range(1, 7) for lg in LANGS)


def test_map_is_puzzle_major_and_stable():
    m = default_marker_map()
    assert m[1] == (1, "python")
    assert m[2] == (1, "bash")
    assert m[3] == (1, "cpp")
    assert m[4] == (2, "python")
    assert m[18] == (6, "cpp")


def test_lookup_known_and_unknown():
    assert lookup(4) == (2, "python")
    assert lookup(999) is None
    assert lookup(0) is None


def test_lookup_custom_mapping():
    assert lookup(7, {7: (3, "bash")}) == (3, "bash")


# --- detection path: only where OpenCV is installed (on-device) ---
def test_detector_round_trip_generated_marker():
    cv2 = pytest.importorskip("cv2")
    pytest.importorskip("numpy")
    import numpy as np

    from vision.markers import MarkerDetector

    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    marker = cv2.aruco.generateImageMarker(dictionary, 4, 200)  # id 4 -> (2, python)
    canvas = np.full((400, 400), 255, dtype=np.uint8)
    canvas[100:300, 100:300] = marker
    frame = cv2.cvtColor(canvas, cv2.COLOR_GRAY2BGR)

    hit = MarkerDetector().pick(frame)
    assert hit is not None
    assert (hit.marker_id, hit.puzzle, hit.lang) == (4, 2, "python")
    assert hit.corners is not None and len(hit.corners) == 4
