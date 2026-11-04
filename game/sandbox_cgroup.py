"""A cgroup v2 child that caps one sandbox run as a whole: total memory, no swap, and
process count, across every process the player's code starts.

The `ulimit` caps in sandbox_runner apply per process (64 processes x 1 GB each would
still exhaust the Pi), so the total needs a cgroup. ocrcade-exec.service runs with
`Delegate=memory pids` and `OCRCADE_SANDBOX_CGROUP=1` (sandbox_runner only builds this
when that is set), which gives the server its own cgroup subtree:

    ocrcade-exec.service/      the delegated root (controllers enabled for children)
      server/                  the server process (cgroup v2 forbids processes in a
                               cgroup whose controllers are enabled for children)
      run/                     each sandbox run; memory.max, swap 0, pids.max

Without delegation, or without the memory controller (Raspberry Pi kernels boot with
`cgroup_disable=memory` unless cmdline.txt adds `cgroup_enable=memory`), setup degrades
and says why in `status`; the per-process ulimits still apply. Pure stdlib.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger("ocrcade.sandbox.cgroup")

CGROUP_FS = Path("/sys/fs/cgroup")
# sh writes its own PID into the run cgroup, then execs the sandbox in place, so the
# sandbox and everything it starts are inside the cap from their first instruction.
_ENTER = 'echo $$ > "$0" && exec "$@"'


def own_cgroup_path(proc_self_cgroup: str) -> str | None:
    """The cgroup v2 path from /proc/self/cgroup (`0::/path`), or None."""
    for line in proc_self_cgroup.splitlines():
        if line.startswith("0::"):
            return line[3:].strip()
    return None


def _oom_kills(events: Path) -> int:
    for line in events.read_text().splitlines():
        key, _, value = line.partition(" ")
        if key == "oom_kill":
            return int(value)
    return 0


class RunCgroup:
    def __init__(self, root: Path, memory_max: int, pids_max: int):
        if memory_max <= 0 or pids_max <= 0:
            raise ValueError("memory_max and pids_max must be positive")
        self.root = Path(root)
        self.memory_max, self.pids_max = memory_max, pids_max
        self.run = self.root / "run"
        self.enabled = False
        self.memory_capped = False
        self.status = "not set up"
        self._kills_before = 0

    @classmethod
    def for_this_process(cls, memory_max: int, pids_max: int) -> RunCgroup:
        path = own_cgroup_path(Path("/proc/self/cgroup").read_text())
        return cls(CGROUP_FS / (path or "/").lstrip("/"), memory_max, pids_max)

    def setup(self) -> bool:
        """Build server/ and run/ under the delegated root. Returns whether runs will be
        confined; never raises."""
        try:
            self._setup()
        except OSError as e:
            self.enabled = self.memory_capped = False
            self.status = f"cgroup not delegated or unusable ({self.root}): {e}"
            log.warning(f"sandbox cgroup disabled: {self.status}")
            return False
        self.enabled = True
        log.info(f"sandbox cgroup: {self.status}")
        return True

    def _setup(self) -> None:
        controllers = set((self.root / "cgroup.controllers").read_text().split())
        wanted = [c for c in ("memory", "pids") if c in controllers]
        if "pids" not in wanted:
            raise OSError("pids controller not available")
        if not os.access(self.root / "cgroup.subtree_control", os.W_OK):
            raise OSError("cgroup.subtree_control is not writable (Delegate=yes missing?)")
        server = self.root / "server"
        server.mkdir(exist_ok=True)
        for pid in (self.root / "cgroup.procs").read_text().split():
            (server / "cgroup.procs").write_text(pid)
        (self.root / "cgroup.subtree_control").write_text(" ".join(f"+{c}" for c in wanted))
        self.run.mkdir(exist_ok=True)
        (self.run / "pids.max").write_text(str(self.pids_max))
        self.memory_capped = "memory" in wanted
        if self.memory_capped:
            (self.run / "memory.max").write_text(str(self.memory_max))
            (self.run / "memory.swap.max").write_text("0")
            (self.run / "memory.oom.group").write_text("1")
            self.status = (f"runs capped at {self.memory_max // (1024 * 1024)} MB, "
                           f"no swap, {self.pids_max} processes")
        else:
            self.status = (f"runs capped at {self.pids_max} processes only: no memory "
                           f"controller (add cgroup_enable=memory to the kernel cmdline)")

    def wrap(self, cmd: list[str]) -> list[str]:
        """The command, made to start inside the run cgroup (unchanged if disabled)."""
        if not self.enabled:
            return list(cmd)
        return ["/bin/sh", "-c", _ENTER, str(self.run / "cgroup.procs"), *cmd]

    def start(self) -> None:
        if self.enabled and self.memory_capped:
            self._kills_before = _oom_kills(self.run / "memory.events")

    def finish(self) -> bool:
        """Kill anything the run left behind; True if the run hit the memory cap."""
        if not self.enabled:
            return False
        try:
            (self.run / "cgroup.kill").write_text("1")
        except OSError as e:
            log.warning(f"could not clear the run cgroup: {e}")
        if not self.memory_capped:
            return False
        return _oom_kills(self.run / "memory.events") > self._kills_before
