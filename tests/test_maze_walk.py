"""The puzzle 3 result replays the player's path through the maze before the result."""
import threading

from PIL import Image, ImageDraw

import server
from display import display_manager as dm
from game.output_validator import _validate_maze_path, maze_walk_for, walk_maze

MAZE = [
    "#####",
    "#S  #",
    "### #",
    "#E  #",
    "#####",
]
SOLUTION = "DDDDRRUURRUURRDDRRUURRDDDDRRDDDDDDLLDDRR"   # the puzzle 3 reference path


def test_a_path_to_e_walks_every_cell_and_is_ok():
    walk = walk_maze(MAZE, "RRDDLL")
    assert walk.cells == ((1, 1), (1, 2), (1, 3), (2, 3), (3, 3), (3, 2), (3, 1))
    assert walk.ok and walk.blocked is None


def test_a_wall_stops_the_walk_where_it_hits():
    walk = walk_maze(MAZE, "RD")
    assert walk.cells == ((1, 1), (1, 2))
    assert walk.blocked == (2, 2) and not walk.ok


def test_a_path_that_ends_short_of_e_is_not_ok():
    walk = walk_maze(MAZE, "RR")
    assert walk.blocked is None and not walk.ok


def test_leaving_the_maze_or_an_unknown_move_blocks():
    assert walk_maze(["S E"], "L").blocked == (0, -1)
    assert walk_maze(MAZE, "RX").blocked == (1, 2)


def test_the_maze_check_agrees_with_the_walk():
    for path in ("RRDDLL", "RD", "RR", "RRDDLLR"):
        assert _validate_maze_path(MAZE, path) == walk_maze(MAZE, path).ok


def test_only_puzzle_3_replays_and_only_with_a_path():
    walk = maze_walk_for(3, SOLUTION + "\n")
    assert walk is not None and walk.ok and len(walk.cells) == len(SOLUTION) + 1
    assert maze_walk_for(4, SOLUTION) is None
    assert maze_walk_for(3, "no moves here\n") is None


def _display() -> dm.Display:
    d = dm.Display.__new__(dm.Display)
    d._lock = threading.Lock()
    d._state, d._state_data, d._state_entered = "executing", {}, 0.0
    return d


def test_a_result_with_a_walk_plays_the_walk_first():
    d = _display()
    walk = maze_walk_for(3, SOLUTION)
    d.success("team", 3, "FLAG{x}", walk=walk)
    assert d._state == "maze_walk"
    assert d._state_data["next"] == ("success", {"team": "team", "puzzle": 3, "flag": "FLAG{x}"})
    d.failure("team", "nope")
    assert d._state == "failure"                     # no walk: straight to the result


def test_a_long_path_is_capped_in_time():
    assert dm._walk_seconds(maze_walk_for(3, SOLUTION)) == len(SOLUTION) * dm.WALK_STEP_S
    long_walk = walk_maze(["S" + " " * 500 + "E"], "R" * 501)
    assert abs(dm._walk_seconds(long_walk) - dm.WALK_MAX_S) < 1e-9


def _frame(walk, t):
    img = Image.new("RGB", (dm.WIDTH, dm.HEIGHT), dm.DARK)
    _display()._draw_maze_walk(ImageDraw.Draw(img), walk, t)
    cell = dm.WALK_CELL
    x = (dm.WIDTH - max(len(row) for row in walk.maze) * cell) // 2
    return img, lambda r, c: (x + c * cell + cell // 2, dm._s(130) + r * cell + cell // 2)


def test_the_dot_moves_along_the_path_and_ends_green_on_e():
    walk = walk_maze(MAZE, "RRDDLL")
    img, center = _frame(walk, 2.5 * dm.WALK_STEP_S)   # halfway from (1,3) down to (2,3)
    x, y = center(1, 3)
    assert img.getpixel((x, y + dm.WALK_CELL // 2)) == dm.AMBER
    img, center = _frame(walk, dm._walk_seconds(walk) + 0.1)
    assert img.getpixel(center(3, 1)) == dm.GREEN        # finished on E


def test_a_wall_hit_ends_with_the_wall_outlined_in_red():
    walk = walk_maze(MAZE, "RD")
    img, center = _frame(walk, dm._walk_seconds(walk) + 0.05)
    x, y = center(2, 2)
    edge = (x - dm.WALK_CELL // 2 + 1, y)                 # the blocked cell's left edge
    assert img.getpixel(edge) == dm.RED


def test_the_server_sends_the_walk_with_the_result(monkeypatch):
    shown = []
    run = {"stdout": SOLUTION[:20] + "\n", "stderr": "", "exit_code": 0,   # stops short of E
           "timed_out": False, "busy": False, "oom": False}
    monkeypatch.setattr(server, "sandbox_run", lambda *_a, **_k: run)
    monkeypatch.setattr(server, "validate", lambda *_a: (False, "Invalid maze path."))
    monkeypatch.setattr(server.display, "received", lambda *_a: None)
    monkeypatch.setattr(server.display, "executing", lambda *_a: None)
    monkeypatch.setattr(server.display, "failure", lambda *a, **k: shown.append((a, k)))
    for puzzle in (3, 1):
        server._finish_submission(puzzle, "team", "python", "code", server.Outcome())
    (args3, kw3), (_, kw1) = shown
    assert args3 == ("team", "Invalid maze path.")
    assert kw3["walk"] is not None and not kw3["walk"].ok
    assert kw1["walk"] is None
