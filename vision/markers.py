"""ArUco puzzle/lang marker cards.

In the ArUco model the organizer prints one physical card per (puzzle, lang) —
7 puzzles (0..6) x 3 langs = 21 cards — each carrying an ArUco marker. The player shows
the card to SELECT a puzzle; the marker ID maps deterministically to (puzzle,
lang). ArUco is built into OpenCV (`cv2.aruco`, no extra dep) and detects
robustly at blur/angle/distance — ideal for a fixed organizer-controlled set.

The ID<->(puzzle, lang) mapping is plain data (pure-Python, CI-testable). cv2 is
imported lazily inside the detector so this module imports without OpenCV.
"""
from __future__ import annotations

from dataclasses import dataclass

LANGS = ("python", "bash", "cpp")
DICT_NAME = "DICT_4X4_50"          # 50 IDs, robust 4x4 markers; we use 1..21


@dataclass(frozen=True)
class PuzzleMarker:
    """A detected puzzle/lang marker. `corners` is the 4x2 image-space quad
    (or None when constructed from a bare lookup)."""
    marker_id: int
    puzzle: int
    lang: str
    corners: object = None


def default_marker_map() -> dict[int, tuple[int, str]]:
    """Canonical ID -> (puzzle, lang). IDs 1..18, ordered puzzle-major:
    id = (puzzle-1)*3 + lang_index + 1, for puzzle 1..6 and LANGS order.

    Puzzle 0 (System Check) came later and takes 19..21, so cards already printed
    for puzzles 1..6 keep their IDs.
    """
    mapping = {
        (p - 1) * 3 + i + 1: (p, lang)
        for p in range(1, 7)
        for i, lang in enumerate(LANGS)
    }
    mapping.update({19 + i: (0, lang) for i, lang in enumerate(LANGS)})
    return mapping


def lookup(marker_id: int, mapping: dict[int, tuple[int, str]] | None = None
           ) -> tuple[int, str] | None:
    """Resolve a marker ID to (puzzle, lang), or None if unmapped."""
    return (mapping or default_marker_map()).get(marker_id)


class MarkerDetector:
    """Detects ArUco puzzle markers in a frame. cv2 loaded lazily on construction
    so the pure mapping helpers above stay importable without OpenCV."""

    def __init__(self, mapping: dict[int, tuple[int, str]] | None = None,
                 dict_name: str = DICT_NAME):
        import cv2  # lazy

        self.mapping = mapping or default_marker_map()
        dictionary = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, dict_name))
        self._detector = cv2.aruco.ArucoDetector(dictionary, cv2.aruco.DetectorParameters())

    def detect(self, frame_bgr) -> list[PuzzleMarker]:
        """Return mapped markers found in the frame, largest first (closest card
        wins when several are visible). Unmapped IDs are ignored."""
        corners, ids, _ = self._detector.detectMarkers(frame_bgr)
        if ids is None:
            return []
        found: list[PuzzleMarker] = []
        for quad, mid in zip(corners, ids.flatten(), strict=True):
            hit = self.mapping.get(int(mid))
            if hit is None:
                continue
            pts = quad.reshape(4, 2)
            found.append(PuzzleMarker(int(mid), hit[0], hit[1], pts))
        found.sort(key=lambda m: _quad_area(m.corners), reverse=True)
        return found

    def pick(self, frame_bgr) -> PuzzleMarker | None:
        """The single most prominent mapped marker, or None."""
        found = self.detect(frame_bgr)
        return found[0] if found else None


def _quad_area(pts) -> float:
    """Shoelace area of a 4-point quad (image pixels)."""
    x = [p[0] for p in pts]
    y = [p[1] for p in pts]
    return 0.5 * abs(
        x[0] * y[1] - x[1] * y[0] + x[1] * y[2] - x[2] * y[1]
        + x[2] * y[3] - x[3] * y[2] + x[3] * y[0] - x[0] * y[3]
    )
