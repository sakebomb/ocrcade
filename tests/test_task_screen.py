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


def test_daemon_sends_the_ready_screen_hint(monkeypatch):
    sent = []
    monkeypatch.setattr(daemon, "_post", lambda url, payload, timeout=5.0: sent.append(payload))
    daemon.ServerUI("http://exec").await_code(1, "python", 0.0, "Too blurry. Hold the paper still.")
    assert sent[0]["ocr_lines"] == ["READY YOUR CODE", "Too blurry. Hold the paper still."]
    assert sent[0]["source_type"] == "ready_code"


def test_ready_screen_puts_title_and_hint_over_a_full_height_camera():
    import base64
    from io import BytesIO

    d = _display("scanning")
    drawn = []
    real = display_manager.Display._centered_text

    def record(self, draw, text, y, *args, **kwargs):
        drawn.append((text, y))
        return real(self, draw, text, y, *args, **kwargs)

    d._centered_text = record.__get__(d)
    buf = BytesIO()
    Image.new("RGB", (320, 240), (200, 0, 0)).save(buf, "JPEG")
    d._thumb_b64 = base64.b64encode(buf.getvalue()).decode()
    img = Image.new("RGB", (display_manager.WIDTH, display_manager.HEIGHT), display_manager.DARK)
    draw = ImageDraw.Draw(img)
    hint = "Can't see your code. Hold it closer so it fills the camera."
    d._draw_ready_screen(img, draw, {"ocr_lines": ["READY YOUR CODE", hint],
                                     "source_type": "ready_code"}, 0.0)
    assert [t for t, _ in drawn] == ["READY YOUR CODE", hint]
    band = display_manager.READY_BAND
    h, w = display_manager.HEIGHT, display_manager.WIDTH
    assert drawn[0][1] < band and drawn[1][1] > h - band        # text sits in the bands
    cam_w = display_manager.READY_CAMERA_W
    assert display_manager.READY_CAMERA_H == h and cam_w == h * 4 // 3   # full height, 4:3
    for y in (band + 5, h // 2, h - band - 5):                   # camera fills between them
        px = img.getpixel((w // 2, y))
        assert px[0] > 150 and px[1] < 60, y
    corner = img.getpixel(((w - cam_w) // 2 + 20, band // 2))   # band drawn over the camera
    assert corner[0] < 60


class _RecordingDraw:
    """An ImageDraw that records each draw.text call as (text, x, y, font)."""

    def __init__(self, img):
        self._draw = ImageDraw.Draw(img)
        self.texts = []

    def text(self, xy, text, *args, font=None, **kwargs):
        self.texts.append((text, xy[0], xy[1], font))
        return self._draw.text(xy, text, *args, font=font, **kwargs)

    def __getattr__(self, name):
        return getattr(self._draw, name)


def _success_texts(flag: str) -> list:
    d = _display("success")
    img = Image.new("RGB", (display_manager.WIDTH, display_manager.HEIGHT), display_manager.DARK)
    draw = _RecordingDraw(img)
    d._draw_success(draw, {"team": "anael", "puzzle": 1, "flag": flag}, 0.0)
    return draw.texts


def test_success_screen_shows_the_flag_large_in_the_top_right():
    texts = _success_texts("FLAG{top_right}")
    flag = [t for t in texts if t[0] == "FLAG{top_right}"]
    assert len(flag) == 1
    _, x, y, font = flag[0]
    assert font is display_manager.FONT_FLAG
    w = display_manager.WIDTH
    probe = ImageDraw.Draw(Image.new("RGB", (w, 10)))
    right = x + probe.textbbox((0, 0), "FLAG{top_right}", font=font)[2]
    assert right == w - display_manager._s(40)                # right-aligned
    congrats_y = next(t[2] for t in texts if "CONGRATULATIONS" in t[0])
    assert y < congrats_y                                      # above the banner


def test_success_screen_without_a_flag_draws_none():
    texts = [t[0] for t in _success_texts("")]
    assert "FLAG" not in texts


def test_camera_feed_keeps_the_full_lores_frame():
    import base64
    from io import BytesIO

    import numpy as np

    thumb = daemon._thumb_b64(np.zeros((480, 640, 3), dtype=np.uint8))
    assert Image.open(BytesIO(base64.b64decode(thumb))).size == (640, 480)


def test_main_page_camera_is_half_again_as_big_and_clears_the_text(monkeypatch):
    import base64
    from io import BytesIO

    # The layout is for kepler's 16:9 monitor; a CI runner's framebuffer may be 4:3.
    monkeypatch.setattr(display_manager, "WIDTH", display_manager.HEIGHT * 16 // 9)
    d = _display("attract")
    d._load_solved_log = lambda: []
    spans = []
    real = display_manager.Display._centered_text

    def record(self, draw, text, y, color, font):
        box = draw.textbbox((0, 0), text, font=font)
        right = (display_manager.WIDTH + box[2] - box[0]) // 2
        spans.append((right, y, y + box[3]))
        return real(self, draw, text, y, color, font)

    d._centered_text = record.__get__(d)
    buf = BytesIO()
    Image.new("RGB", (640, 480), (200, 0, 0)).save(buf, "JPEG")
    d._thumb_b64 = base64.b64encode(buf.getvalue()).decode()
    img = Image.new("RGB", (display_manager.WIDTH, display_manager.HEIGHT), display_manager.DARK)
    d._draw_main_page(img, ImageDraw.Draw(img), 0.0)

    cw, ch = display_manager.MAIN_CAMERA
    assert (cw, ch) == (display_manager._s(480), display_manager._s(360))
    pad = display_manager._s(12)
    left, top = display_manager.WIDTH - cw - pad, pad
    px = img.getpixel((left + cw // 2, top + ch - 5))   # bottom of the 1.5x camera
    assert px[0] > 150 and px[1] < 60
    for right, y0, y1 in spans:                          # no text under the camera
        if y0 < top + ch and y1 > top:
            assert right < left, (right, y0)
