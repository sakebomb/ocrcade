"""The reference solutions people copy by hand must actually solve their puzzles.

If a reference is wrong, every handwritten copy of it scores as a failed read even when
the reader is perfect. Runs the solutions with `/puzzle` pointed at the repo's puzzle
folders (or a stand-in), without the sandbox.
"""
import subprocess
import sys
from pathlib import Path

import pytest

from evaluation.solutions import PUZZLES, reference
from game.output_validator import _extract_path, _validate_maze_path, validate

ROOT = Path(__file__).resolve().parent.parent


def _run(code: str, puzzle_dir: Path) -> str:
    code = code.replace("/puzzle/", f"{puzzle_dir}/")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         timeout=10, check=True)
    return out.stdout


def test_every_reference_names_a_team():
    for p in PUZZLES:
        assert reference(p).startswith("# team: ")


def test_puzzle0_reference_passes_the_validator(tmp_path):
    ok, _ = validate(0, _run(reference(0), tmp_path))
    assert ok


def test_puzzle1_reference_prints_the_flag_file(tmp_path):
    # The real flag file is event data and not in git, so a stand-in takes its place.
    (tmp_path / "flag1.txt").write_text("FLAG{test}\n")
    assert "FLAG{test}" in _run(reference(1), tmp_path)


def test_puzzle2_reference_passes_the_validator(monkeypatch):
    monkeypatch.setattr("game.output_validator._read_flag", lambda n: f"flag{n}")
    ok, _ = validate(2, _run(reference(2), ROOT / "puzzles" / "level2"))
    assert ok


def test_puzzle3_reference_walks_the_fixed_maze():
    maze = (ROOT / "puzzles" / "level3" / "maze_fixed.txt").read_text().splitlines()
    path = _extract_path(_run(reference(3), ROOT / "puzzles" / "level3"))
    assert path is not None and _validate_maze_path(maze, path)


def test_unknown_puzzle_has_no_reference():
    with pytest.raises(KeyError):
        reference(4)
