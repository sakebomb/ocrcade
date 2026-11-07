"""A timed-out submission must not score when the worker finishes late."""
import threading

import server


def test_outcome_paints_once():
    paints = []
    outcome = server.Outcome()
    assert outcome.claim(lambda: paints.append("a")) is True
    assert outcome.claim(lambda: paints.append("b")) is False
    assert paints == ["a"]


def test_a_progress_screen_does_not_cover_the_terminal_one():
    paints = []
    outcome = server.Outcome()
    assert outcome.claim(lambda: paints.append("fail")) is True
    assert outcome.paint_if_open(lambda: paints.append("exec")) is False
    assert paints == ["fail"]


def test_a_paint_that_raises_does_not_close_the_outcome():
    outcome = server.Outcome()

    def boom() -> None:
        raise RuntimeError("rtb")

    try:
        outcome.claim(boom)
    except RuntimeError:
        pass
    paints = []
    assert outcome.claim(lambda: paints.append("err")) is True
    assert paints == ["err"]


def test_late_finish_does_not_score_after_the_watchdog(monkeypatch):
    release = threading.Event()
    screens = []
    submits = []

    def slow_run(*_a, **_k):
        release.wait(2)
        return {"stdout": "hi", "stderr": "", "exit_code": 0,
                "timed_out": False, "busy": False, "oom": False}

    monkeypatch.setattr(server, "is_solved", lambda *_a, **_k: False)
    monkeypatch.setattr(server, "sandbox_run", slow_run)
    monkeypatch.setattr(server, "validate", lambda *_a, **_k: (True, "flag"))
    monkeypatch.setattr(server, "rtb_submit",
                        lambda *a, **_k: submits.append(a) or (True, "ok"))
    progress = []
    workers = []

    class Capture(threading.Thread):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            workers.append(self)

    monkeypatch.setattr(server.threading, "Thread", Capture)
    monkeypatch.setattr(server.display, "failure", lambda *_a: screens.append("fail"))
    monkeypatch.setattr(server.display, "success", lambda *_a: screens.append("ok"))
    monkeypatch.setattr(server.display, "received", lambda *_a: progress.append("recv"))
    monkeypatch.setattr(server.display, "executing", lambda *_a: progress.append("exec"))
    monkeypatch.setattr(server, "SUBMISSION_WATCHDOG", 0.05)

    server._watchdog(1, "late-team", "python", "print(1)")
    assert screens == ["fail"]
    assert submits == []
    release.set()
    assert len(workers) == 1
    workers[0].join(2)
    assert not workers[0].is_alive()
    assert screens == ["fail"]
    assert submits == []
    assert "exec" not in progress


def test_a_solve_puts_its_flag_on_the_success_screen(monkeypatch):
    shown = []
    monkeypatch.setattr(server, "rtb_submit", lambda *_a, **_k: (True, "ok"))
    monkeypatch.setattr(server, "_append_solved_log", lambda *_a: None)
    monkeypatch.setattr(server.display, "success", lambda *a: shown.append(a))
    server._score(1, "team", "FLAG{shown}", server.Outcome())
    assert shown == [("team", 1, "FLAG{shown}")]
