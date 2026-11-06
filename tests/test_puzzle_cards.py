"""The 6-up cut sheets still carry a decodable marker for every puzzle and language."""
import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("cv2")

from vision.markers import MarkerDetector  # noqa: E402


def _sheets():
    path = Path(__file__).resolve().parents[1] / "tools" / "make_puzzle_cards.py"
    spec = importlib.util.spec_from_file_location("make_puzzle_cards", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_six_up_pages_decode_every_card():
    sheets = _sheets()
    pages = sheets.build_six_up_pages()
    assert len(pages) == 3
    assert pages[0].shape == (sheets.LETTER_H, sheets.LETTER_W, 3)
    detector = MarkerDetector()
    found = [hit for page in pages for hit in detector.detect(page)]
    assert len(found) == 18
    assert {(hit.puzzle, hit.lang) for hit in found} == {
        (puzzle, lang)
        for puzzle in range(1, 7)
        for lang in ("python", "bash", "cpp")
    }
