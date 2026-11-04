"""The sandbox's run cgroup, exercised on a fake cgroup tree (plain files in tmp_path).
Real cgroupfs behaviour (moving processes, OOM kills) is covered on kepler by
tools/test_sandbox.py."""
from pathlib import Path

import pytest

from game.sandbox_cgroup import RunCgroup, own_cgroup_path

MB = 1024 * 1024


def fake_root(tmp_path: Path, controllers="cpuset cpu io memory pids", pids="1234\n"):
    root = tmp_path / "system.slice" / "ocrcade-exec.service"
    root.mkdir(parents=True)
    (root / "cgroup.controllers").write_text(controllers + "\n")
    (root / "cgroup.subtree_control").write_text("")
    (root / "cgroup.procs").write_text(pids)
    return root


def make_children(root: Path):
    """cgroupfs creates these files itself when a child is made; the fake must too."""
    for child in ("server", "run"):
        d = root / child
        d.mkdir(exist_ok=True)
        for f in ("cgroup.procs", "memory.max", "memory.swap.max", "pids.max",
                  "memory.oom.group", "cgroup.kill"):
            (d / f).touch()
        (d / "memory.events").write_text("low 0\nhigh 0\nmax 0\noom 0\noom_kill 0\n")


def test_own_cgroup_path_reads_the_v2_line():
    text = "0::/system.slice/ocrcade-exec.service\n"
    assert own_cgroup_path(text) == "/system.slice/ocrcade-exec.service"


def test_own_cgroup_path_is_none_without_a_v2_line():
    assert own_cgroup_path("12:memory:/foo\n") is None


def test_setup_moves_the_server_out_and_caps_the_run_cgroup(tmp_path):
    root = fake_root(tmp_path)
    make_children(root)
    cg = RunCgroup(root, memory_max=1024 * MB, pids_max=64)
    assert cg.setup() and cg.memory_capped
    assert (root / "server" / "cgroup.procs").read_text() == "1234"
    assert set((root / "cgroup.subtree_control").read_text().split()) == {"+memory", "+pids"}
    run = root / "run"
    assert (run / "memory.max").read_text() == str(1024 * MB)
    assert (run / "memory.swap.max").read_text() == "0"
    assert (run / "pids.max").read_text() == "64"
    assert (run / "memory.oom.group").read_text() == "1"


def test_without_the_memory_controller_only_pids_are_capped(tmp_path):
    root = fake_root(tmp_path, controllers="cpuset cpu io pids")
    make_children(root)
    cg = RunCgroup(root, memory_max=1024 * MB, pids_max=64)
    assert cg.setup() and not cg.memory_capped
    assert (root / "cgroup.subtree_control").read_text().split() == ["+pids"]
    assert (root / "run" / "memory.max").read_text() == ""
    assert "memory controller" in cg.status


def test_setup_fails_soft_when_the_cgroup_is_not_delegated(tmp_path):
    root = fake_root(tmp_path)
    (root / "cgroup.subtree_control").chmod(0o444)
    cg = RunCgroup(root, memory_max=1024 * MB, pids_max=64)
    try:
        assert not cg.setup()
        assert cg.wrap(["python3", "x.py"]) == ["python3", "x.py"]
        assert "not delegated" in cg.status
        assert not (root / "server").exists()  # failed before moving any process
    finally:
        (root / "cgroup.subtree_control").chmod(0o644)


def test_wrap_enters_the_run_cgroup_before_exec(tmp_path):
    root = fake_root(tmp_path)
    make_children(root)
    cg = RunCgroup(root, memory_max=1024 * MB, pids_max=64)
    cg.setup()
    cmd = cg.wrap(["bwrap", "--", "python3"])
    assert cmd[:2] == ["/bin/sh", "-c"]
    assert 'echo $$ > "$0"' in cmd[2] and 'exec "$@"' in cmd[2]
    assert cmd[3] == str(root / "run" / "cgroup.procs")
    assert cmd[4:] == ["bwrap", "--", "python3"]


def test_finish_kills_leftovers_and_reports_a_new_oom_kill(tmp_path):
    root = fake_root(tmp_path)
    make_children(root)
    cg = RunCgroup(root, memory_max=1024 * MB, pids_max=64)
    cg.setup()
    cg.start()
    (root / "run" / "memory.events").write_text("oom 1\noom_kill 1\n")
    assert cg.finish() is True
    assert (root / "run" / "cgroup.kill").read_text() == "1"
    cg.start()
    assert cg.finish() is False  # same count as before this run: no new kill
    cg.start()
    (root / "run" / "memory.events").write_text("oom 5\noom_kill 1\n")
    assert cg.finish() is False  # the limit was hit but nothing was killed


@pytest.mark.parametrize("bad", [0, -5])
def test_rejects_nonsense_limits(tmp_path, bad):
    with pytest.raises(ValueError):
        RunCgroup(tmp_path, memory_max=bad, pids_max=64)
