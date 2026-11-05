"""Fruit Ninja must not turn a crash into a submission."""
from pathlib import Path

from challenges import fruit_ninja_runner as runner


def test_player_result_submits_only_a_win():
    assert runner.player_result(True, 100) == ("submit", "FRUITAGE_SOLVED")
    assert runner.player_result(False, None) == ("feedback", "Fruit Ninja did not start")
    kind, detail = runner.player_result(False, 40)
    assert kind == "feedback"
    assert "40" in detail


def test_missing_camera_element_does_not_launch(monkeypatch):
    monkeypatch.setattr(runner, "_have_libcamerasrc", lambda: False)

    def launched(*_a, **_k):
        raise AssertionError("game launched")

    monkeypatch.setattr(runner.subprocess, "run", launched)
    assert runner.run("http://127.0.0.1:5000/submit") == (False, None)


def test_nonzero_exit_is_not_a_score(monkeypatch, tmp_path):
    path = tmp_path / "result.json"
    monkeypatch.setattr(runner, "_have_libcamerasrc", lambda: True)
    monkeypatch.setattr(runner.requests, "post", lambda *_a, **_k: None)
    monkeypatch.setattr(runner.tempfile, "mktemp", lambda suffix="": str(path))

    class Proc:
        returncode = 1

    monkeypatch.setattr(runner.subprocess, "run", lambda *_a, **_k: Proc())
    assert runner.run("http://127.0.0.1:5000/submit") == (False, None)


def test_finished_low_score_is_a_score(monkeypatch, tmp_path):
    path = tmp_path / "result.json"
    monkeypatch.setattr(runner, "_have_libcamerasrc", lambda: True)
    monkeypatch.setattr(runner.requests, "post", lambda *_a, **_k: None)
    monkeypatch.setattr(runner.tempfile, "mktemp", lambda suffix="": str(path))

    class Proc:
        returncode = 0

    def finish(*_a, **_k):
        Path(path).write_text('{"score": 40}')
        return Proc()

    monkeypatch.setattr(runner.subprocess, "run", finish)
    assert runner.run("http://127.0.0.1:5000/submit") == (False, 40)
