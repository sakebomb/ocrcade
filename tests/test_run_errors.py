"""A failed run shows the player its error, or what it printed, so they can fix it."""
import threading

from PIL import Image, ImageDraw

import server
from display import display_manager as dm
from game.run_errors import CRASHED, DETAIL_LINES, RunDetail, explain

TRACEBACK = (
    "Traceback (most recent call last):\n"
    '  File "/sandbox/solution.py", line 5, in <module>\n'
    "    print(totl)\n"
    "          ^^^^\n"
    "NameError: name 'totl' is not defined. Did you mean: 'total'?\n"
)


def test_a_crash_shows_the_end_of_its_traceback():
    headline, detail = explain({"stdout": "", "stderr": TRACEBACK, "exit_code": 1}, "x")
    assert headline == CRASHED and detail.label == "ERROR"
    assert detail.lines[-1].startswith("NameError: name 'totl'")
    assert "          ^^^^" in detail.lines                  # the marker keeps its indent


def test_a_long_traceback_keeps_only_its_last_lines():
    stderr = "".join(f"  frame {i}\n" for i in range(20)) + "ValueError: bad\n"
    _, detail = explain({"stderr": stderr, "exit_code": 1}, "x")
    assert len(detail.lines) == DETAIL_LINES and detail.lines[-1] == "ValueError: bad"


def test_a_compile_error_shows_its_error_lines():
    stderr = ("COMPILE ERROR:\n/sandbox/solution.cpp: In function 'int main()':\n"
              "/sandbox/solution.cpp:3:5: error: 'cout' was not declared in this scope\n"
              "    3 |     cout << 1;\n")
    _, detail = explain({"stderr": stderr, "exit_code": 1}, "x")
    assert detail.lines == ("/sandbox/solution.cpp:3:5: error: 'cout' was not declared "
                            "in this scope",)


def test_wrong_output_shows_what_the_code_printed():
    headline, detail = explain({"stdout": "12345\n", "stderr": "", "exit_code": 0},
                               "Expected output not found. Check your math.")
    assert headline == "Expected output not found. Check your math."
    assert detail == RunDetail("YOUR OUTPUT", ("12345",))


def test_a_warning_on_a_clean_exit_is_not_a_crash():
    headline, detail = explain({"stdout": "7\n", "stderr": "warning\n", "exit_code": 0}, "m")
    assert headline == "m" and detail.label == "YOUR OUTPUT"


def test_no_output_and_no_error_shows_just_the_message():
    assert explain({"stdout": "", "stderr": "", "exit_code": 0}, "m") == ("m", None)


def test_the_server_sends_the_error_with_the_failure(monkeypatch):
    shown = []
    run = {"stdout": "", "stderr": TRACEBACK, "exit_code": 1,
           "timed_out": False, "busy": False, "oom": False}
    monkeypatch.setattr(server, "sandbox_run", lambda *_a, **_k: run)
    monkeypatch.setattr(server, "validate", lambda *_a: (False, "no output"))
    monkeypatch.setattr(server.display, "received", lambda *_a: None)
    monkeypatch.setattr(server.display, "executing", lambda *_a: None)
    monkeypatch.setattr(server.display, "failure", lambda *a, **k: shown.append((a, k)))
    server._finish_submission(2, "team", "python", "code", server.Outcome())
    (args, kw), = shown
    assert args == ("team", CRASHED) and kw["detail"].lines[-1].startswith("NameError")


def test_a_failure_with_an_error_stays_up_long_enough_to_read():
    assert dm._fail_reset({"detail": RunDetail("ERROR", ("x",))}) == dm.FAIL_DETAIL_RESET
    assert dm._fail_reset({"detail": None}) == dm.FAIL_RESET
    assert dm.FAIL_DETAIL_RESET > dm.FAIL_RESET


def _failure_img(detail):
    img = Image.new("RGB", (dm.WIDTH, dm.HEIGHT), dm.DARK)
    d = dm.Display.__new__(dm.Display)
    d._lock = threading.Lock()
    d._draw_failure(ImageDraw.Draw(img), {"message": "m", "detail": detail}, 0.0)
    return img


def _white_xs(img):
    return [x for x in range(dm.WIDTH) for y in range(0, dm.HEIGHT, 2)
            if img.getpixel((x, y)) == dm.WHITE]


def test_the_failure_screen_draws_the_error_lines():
    assert _white_xs(_failure_img(None)) == []
    assert _white_xs(_failure_img(RunDetail("ERROR", ("NameError: name 'totl'",))))


def test_the_error_box_keeps_the_marker_under_the_mistake():
    # Solid letters: the thin ^ marks are anti-aliased and never pure white.
    flush = min(_white_xs(_failure_img(RunDetail("ERROR", ("HHHH",)))))
    indented = min(_white_xs(_failure_img(RunDetail("ERROR", ("          HHHH",)))))
    assert indented > flush + dm._s(60)
