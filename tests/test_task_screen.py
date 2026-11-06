"""The puzzle task screen and the main page while waiting for a card. No device needed."""
import threading
from pathlib import Path

from PIL import Image, ImageDraw

import daemon
from display import display_manager
from game.puzzle_tasks import TASKS, task_lines

ROOT = Path(__file__).resolve().parents[1]


def _display(state: str) -> display_manager.Display:
    """A Display without its framebuffer or render thread."""
    d = display_manager.Display.__new__(display_manager.Display)
    d._lock = threading.Lock()
    d._state, d._state_data, d._state_entered, d._last_preview = state, {}, 0.0, 0.0
    return d


def test_every_card_puzzle_has_a_task_and_a_next_step():
    for puzzle in range(1, 7):
        lines = task_lines(puzzle)
        assert len(lines) >= 2, puzzle
    assert "show your code" in task_lines(2)[-1]
    assert "Keep the card up" in task_lines(5)[-1]


def test_tasks_do_not_give_away_the_puzzle_2_answer():
    answer = (ROOT / "puzzles" / "level2" / "expected.txt").read_text().strip()
    text = " ".join(line for lines in TASKS.values() for line in lines)
    assert answer not in text


def test_waiting_for_a_card_shows_the_main_page():
    d = _display("scanning")
    d.preview([], 0.0, 1.0, "await_card")
    assert d._state == "attract"


def test_a_scanned_card_leaves_the_main_page_for_its_task():
    d = _display("attract")
    d.preview(["PUZZLE 2", "PYTHON", *task_lines(2)], 0.0, 1.0, "puzzle_seen")
    assert d._state == "scanning"
    assert d._state_data["source_type"] == "puzzle_seen"


def test_waiting_for_a_card_does_not_cover_a_result_screen():
    d = _display("success")
    d.preview([], 0.0, 1.0, "await_card")
    assert d._state == "success"


def test_task_screen_draws_the_task():
    d = _display("scanning")
    drawn = []
    real = display_manager.Display._centered_text

    def record(self, draw, text, *args, **kwargs):
        drawn.append(text)
        return real(self, draw, text, *args, **kwargs)

    d._centered_text = record.__get__(d)
    img = Image.new("RGB", (display_manager.WIDTH, display_manager.HEIGHT), display_manager.DARK)
    lines = ["PUZZLE 2", "PYTHON", *task_lines(2)]
    d._draw_task(ImageDraw.Draw(img), {"ocr_lines": lines}, 0.0)
    assert drawn == lines


def test_main_page_list_ends_above_the_hall_of_fame_line():
    d = _display("attract")
    d._load_solved_log = lambda: [{"team": "anael", "name": "Memory Dump", "ts": "18:02"}]
    rows = []
    real = display_manager.Display._centered_text

    def record(self, draw, text, y, *args, **kwargs):
        rows.append((text, y))
        return real(self, draw, text, y, *args, **kwargs)

    d._centered_text = record.__get__(d)
    img = Image.new("RGB", (display_manager.WIDTH, display_manager.HEIGHT), display_manager.DARK)
    d._draw_attract(ImageDraw.Draw(img), 0.0)
    *listed, (_, hof_y) = rows
    last_y = listed[-1][1]
    assert listed[-1][0] == ">>> SHOW A PUZZLE CARD TO START <<<"
    assert last_y + display_manager._s(48) <= hof_y


def test_main_page_puzzle_rows_line_up():
    rows = [line for line in display_manager.ATTRACT_LINES if line.startswith("[ PUZZLE")]
    assert len(rows) == 7
    assert len({len(row) for row in rows}) == 1


def test_read_error_screen_has_no_reset_countdown():
    # A failed read keeps the puzzle and reads again; a countdown restarted on
    # every retry (5 4 3 5 4 3 ...).
    d = _display("parse_error")
    drawn = []
    real = display_manager.Display._centered_text

    def record(self, draw, text, *args, **kwargs):
        drawn.append(text)
        return real(self, draw, text, *args, **kwargs)

    d._centered_text = record.__get__(d)
    img = Image.new("RGB", (display_manager.WIDTH, display_manager.HEIGHT), display_manager.DARK)
    data = {"message": "no text found", "ocr_lines": ["CARD NOT READ", "no text found"]}
    d._draw_parse_error(ImageDraw.Draw(img), data, 2.0)
    assert not any("RESETTING" in text or "IN 3s" in text for text in drawn)
    assert any("try again" in text.lower() for text in drawn)


def test_daemon_sends_the_task_when_a_card_locks(monkeypatch):
    sent = []
    monkeypatch.setattr(daemon, "_post", lambda url, payload, timeout=5.0: sent.append(payload))
    ui = daemon.ServerUI("http://exec")
    ui.puzzle_locked(2, "python")
    ui.puzzle_locked(6, "python")
    ui.await_card()
    assert sent[0]["ocr_lines"] == ["PUZZLE 2", "PYTHON", *task_lines(2)]
    assert sent[1]["ocr_lines"][1] == "CAMERA GAME"
    assert sent[2]["source_type"] == "await_card"
