"""One sandbox run at a time: a second submission that cannot get the slot within
SLOT_WAIT is told the booth is busy, instead of starting a second sandbox. Fakes the
execution, so no bwrap is needed (the real sandbox is tested on kepler)."""
import threading

from game import sandbox_runner


def test_second_run_while_one_is_running_is_busy(monkeypatch):
    started, release = threading.Event(), threading.Event()

    def slow(puzzle, lang, code, workdir):
        started.set()
        release.wait(5)
        return sandbox_runner._result(stdout="done")

    monkeypatch.setattr(sandbox_runner, "_execute", slow)
    monkeypatch.setattr(sandbox_runner, "SLOT_WAIT", 0.2)
    first = {}
    t = threading.Thread(target=lambda: first.update(sandbox_runner.run(1, "python", "x")))
    t.start()
    assert started.wait(5)
    second = sandbox_runner.run(1, "python", "y")
    release.set()
    t.join(5)
    assert second["busy"] and not second["timed_out"] and second["stdout"] == ""
    assert first["stdout"] == "done" and not first["busy"]


def test_the_slot_is_released_after_a_run_even_if_it_raises(monkeypatch):
    def boom(*a):
        raise RuntimeError("bad")

    monkeypatch.setattr(sandbox_runner, "_execute", boom)
    try:
        sandbox_runner.run(1, "python", "x")
    except RuntimeError:
        pass
    monkeypatch.setattr(sandbox_runner, "_execute",
                        lambda *a: sandbox_runner._result(stdout="ok"))
    monkeypatch.setattr(sandbox_runner, "SLOT_WAIT", 0.2)
    assert sandbox_runner.run(1, "python", "x")["stdout"] == "ok"


def test_every_result_has_the_same_keys():
    keys = {"stdout", "stderr", "exit_code", "timed_out", "busy", "oom"}
    assert set(sandbox_runner._result()) == keys
    assert set(sandbox_runner._err("x")) == keys
