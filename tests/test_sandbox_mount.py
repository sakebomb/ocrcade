"""The sandbox shows a player their puzzle inputs, and nothing the validator keeps."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from game import sandbox_runner


class _Proc:
    stdout = ""
    stderr = ""
    returncode = 0


def _puzzle_source(cmd: list) -> Path | None:
    for index, arg in enumerate(cmd):
        if arg == "--ro-bind" and cmd[index + 2] == "/puzzle":
            return Path(cmd[index + 1])
    return None


@pytest.fixture
def mounted(tmp_path, monkeypatch):
    def run(puzzle: int, lang: str, files: dict[str, str]) -> dict:
        level = tmp_path / f"level{puzzle}-{lang}"
        level.mkdir()
        for name, text in files.items():
            (level / name).write_text(text)
        work = tmp_path / f"work-{puzzle}-{lang}"
        work.mkdir()
        seen: dict = {}

        def capture(cmd, _timeout, cwd=None):
            src = _puzzle_source(cmd)
            if src is not None:
                seen["names"] = sorted(path.name for path in src.iterdir())
                seen["is_level"] = src.resolve() == level.resolve()
            return _Proc(), False

        monkeypatch.setattr(sandbox_runner, "_puzzle_data_dir", lambda _puzzle: level)
        monkeypatch.setattr(sandbox_runner, "_confined", capture)
        sandbox_runner._execute(puzzle, lang, "print(1)\n", str(work))
        return seen

    return run


@pytest.mark.parametrize("lang", ["python", "bash", "cpp"])
def test_puzzle2_mount_hides_the_answer(mounted, lang):
    seen = mounted(2, lang, {"data.txt": "1\n", "expected.txt": "secret\n"})
    assert seen["names"] == ["data.txt"]
    assert seen["is_level"] is False


@pytest.mark.parametrize("puzzle,visible,hidden", [
    (1, "flag1.txt", "flag1.example.txt"),
    (3, "maze_fixed.txt", "expected.txt"),
    (4, "maze_fixed.txt", "solution.txt"),
    (0, None, "expected.txt"),
])
def test_other_levels_hide_validator_files(mounted, puzzle, visible, hidden):
    files = {hidden: "secret\n"}
    if visible is not None:
        files[visible] = "player\n"
    seen = mounted(puzzle, "python", files)
    assert seen["names"] == ([] if visible is None else [visible])
    assert seen["is_level"] is False
