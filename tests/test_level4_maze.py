"""Puzzle 4 shows players the same maze the validator checks, and not the solution."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from game import output_validator as ov
from game.maze_generator import generate_maze


def test_published_maze_matches_the_validator_and_hides_the_solution(tmp_path, monkeypatch):
    monkeypatch.setattr(ov, "_read_flag", lambda _n: "FLAG")
    maze, solution = generate_maze(15, 15, seed=1)
    dest = tmp_path / "maze_fixed.txt"

    ov.publish_level4_maze(maze, solution, dest)

    shown = dest.read_text().splitlines()
    assert shown == ov._current_maze == maze
    assert solution not in dest.read_text()
    assert ov.validate(4, solution)[0] is True


def test_reset_replaces_the_maze_players_see(tmp_path, monkeypatch):
    import server

    dest = tmp_path / "maze_fixed.txt"
    monkeypatch.setattr(server, "LEVEL4_MAZE", dest)
    mazes = [generate_maze(15, 15, seed=1), generate_maze(15, 15, seed=2)]
    assert mazes[0][0] != mazes[1][0]
    pending = iter(mazes)
    monkeypatch.setattr(server, "generate_maze", lambda rows, cols: next(pending))

    server._init_level4_maze()
    assert dest.read_text().splitlines() == ov._current_maze == mazes[0][0]
    server._init_level4_maze()
    assert dest.read_text().splitlines() == ov._current_maze == mazes[1][0]
    assert mazes[0][1] not in dest.read_text()
    assert mazes[1][1] not in dest.read_text()


def test_preview_shows_the_maze_the_validator_checks(tmp_path, monkeypatch):
    import server

    maze, solution = generate_maze(15, 15, seed=3)
    level = tmp_path / "level4"
    level.mkdir()
    ov.publish_level4_maze(maze, solution, level / "maze_fixed.txt")
    monkeypatch.setattr(server, "PUZZLES_DIR", tmp_path)
    shown: dict[str, str] = {}
    monkeypatch.setattr(
        server.display, "maze_preview", lambda text: shown.__setitem__("text", text))

    server._show_maze_preview(4, "ANONYMOUS")

    assert shown["text"].splitlines() == ov._current_maze
