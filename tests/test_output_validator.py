"""Unit tests for the maze validation logic — the trickiest pure code in the
exec layer, and the puzzle most exposed to VLM misreads."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from game.output_validator import _extract_path, _validate_maze_path

MAZE = [
    "S.#",
    "#.#",
    "#.E",
]
# S(0,0) -> D(1,0)? wall '#'. Correct walk: R to (0,1), D to (1,1), D to (2,1), R to (2,2)=E
SOLUTION = "RDDR"


def test_extract_path_clean_run():
    assert _extract_path("RRDDLLUU") == "RRDDLLUU"


def test_extract_path_strips_spaces():
    assert _extract_path("RR DD LL UU") == "RRDDLLUU"


def test_extract_path_ignores_short_words():
    assert _extract_path("hello") is None


def test_extract_path_rejects_non_directions():
    assert _extract_path("ABCDEFGH") is None


def test_extract_path_no_ocr_lookalike_translation():
    # The old Tesseract-era code translated O/0 -> D; we must NOT do that now.
    assert _extract_path("OOOOOOOO") is None


def test_extract_path_found_on_its_own_line():
    # Real usage: the player's program prints the path (optionally after a label
    # on a separate line). Newlines isolate the run from surrounding words.
    assert _extract_path("Solution:\nRDDRRDDR\n") == "RDDRRDDR"


def test_validate_maze_path_accepts_solution():
    assert _validate_maze_path(MAZE, SOLUTION) is True


def test_validate_maze_path_rejects_wall_hit():
    assert _validate_maze_path(MAZE, "DDDD") is False


def test_validate_maze_path_rejects_out_of_bounds():
    assert _validate_maze_path(MAZE, "LLLLLLLL") is False


def test_validate_maze_path_rejects_wrong_endpoint():
    assert _validate_maze_path(MAZE, "RD") is False  # stops mid-maze, not at E
