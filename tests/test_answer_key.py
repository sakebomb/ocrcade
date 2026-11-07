"""Answer slips are the reference programs and nothing else."""
import importlib.util
from pathlib import Path

from evaluation.solutions import PUZZLES, reference


def _key():
    path = Path(__file__).resolve().parents[1] / "tools" / "make_answer_key.py"
    spec = importlib.util.spec_from_file_location("make_answer_key", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_answer_slips_are_the_reference_programs_only():
    slips = _key().answer_slips()
    assert [puzzle for puzzle, _ in slips] == list(PUZZLES)
    for puzzle, code in slips:
        assert code == reference(puzzle)
        assert code.startswith("# team: ocrcade\n")
        assert "capture_samples" not in code


def test_puzzle_3_answer_is_wrapped_and_still_solves_the_maze():
    import subprocess
    import sys

    from game.output_validator import PUZZLES_DIR, _extract_path, _validate_maze_path

    code = reference(3)
    assert max(len(line) for line in code.splitlines()) <= 30   # short enough to read
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         check=True).stdout
    maze = (PUZZLES_DIR / "level3" / "maze_fixed.txt").read_text().splitlines()
    path = _extract_path(out)
    assert path is not None and _validate_maze_path(maze, path)
