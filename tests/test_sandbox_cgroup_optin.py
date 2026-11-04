"""The run cgroup is opt-in: without OCRCADE_SANDBOX_CGROUP=1 the runner never touches
this process's cgroup (on a desktop, setup would rearrange the terminal's scope)."""
from pathlib import Path

import pytest

from game import sandbox_runner
from game.sandbox_cgroup import RunCgroup


@pytest.fixture
def setups(monkeypatch):
    """Records whether the runner located and set up a real cgroup (both faked)."""
    calls = []

    def locate(memory_max, pids_max):
        calls.append("located")
        return RunCgroup(Path("/nonexistent"), memory_max, pids_max)

    def setup(_self):
        calls.append("setup")
        return False

    monkeypatch.setattr(sandbox_runner, "_cgroup", None)
    monkeypatch.setattr(RunCgroup, "for_this_process", staticmethod(locate))
    monkeypatch.setattr(RunCgroup, "setup", setup)
    return calls


@pytest.mark.parametrize("value", [None, "", "0", "yes"])
def test_without_the_opt_in_the_cgroup_is_never_set_up(monkeypatch, setups, value):
    if value is None:
        monkeypatch.delenv(sandbox_runner.CGROUP_ENV, raising=False)
    else:
        monkeypatch.setenv(sandbox_runner.CGROUP_ENV, value)
    cg = sandbox_runner.run_cgroup()
    assert setups == []
    assert not cg.enabled and not cg.memory_capped
    assert sandbox_runner.CGROUP_ENV in cg.status
    assert cg.wrap(["python3", "x.py"]) == ["python3", "x.py"]


def test_with_the_opt_in_the_cgroup_is_set_up_once(monkeypatch, setups):
    monkeypatch.setenv(sandbox_runner.CGROUP_ENV, "1")
    first = sandbox_runner.run_cgroup()
    assert sandbox_runner.run_cgroup() is first
    assert setups == ["located", "setup"]
