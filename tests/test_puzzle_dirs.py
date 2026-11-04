"""Every code puzzle needs its folder in git: the sandbox refuses to run without it.

Git does not track empty folders, so levels with no data files (0, the test puzzle, and 4,
whose maze lives in memory) carry a .gitkeep. Without it a fresh checkout answers
"No data for puzzle N" to every submission.
"""
from pathlib import Path

import pytest

PUZZLES_DIR = Path(__file__).resolve().parent.parent / "puzzles"


@pytest.mark.parametrize("level", range(0, 5))
def test_code_puzzle_folder_exists(level):
    assert (PUZZLES_DIR / f"level{level}").is_dir()
