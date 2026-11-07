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
