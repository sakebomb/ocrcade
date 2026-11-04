"""
Run player-submitted (untrusted) code inside a bubblewrap (bwrap) sandbox.

Hardening (see SECURITY review):
- --clearenv + explicit PATH: player code never sees the server's environment
  (which may hold API tokens / RTB credentials).
- --unshare-user --uid/--gid 65534: runs as nobody in a user namespace, so even a
  sandbox escape lands as an unprivileged, group-less UID.
- --unshare-{net,pid,uts,ipc} + --die-with-parent + --new-session: full namespace
  isolation, no orphan survival, own session (can't signal the server's pgroup).
- in-sandbox `ulimit` preamble: caps procs (fork bombs), address space, file size,
  and CPU time. (bwrap 0.11 has no --rlimit; ulimit via /bin/sh is thread-safe,
  unlike subprocess preexec_fn in a threaded server.)
- C++ is compiled INSIDE the sandbox too, so malicious source can't #include host
  files as a read-oracle.
- One run at a time (a busy slot is reported, not queued). With OCRCADE_SANDBOX_CGROUP=1
  (ocrcade-exec.service sets it; see _make_cgroup), every command starts inside a
  cgroup capping the whole run: 1 GB of memory, no swap, 64 processes
  (game/sandbox_cgroup.py). The per-process ulimits alone would let 64 processes take
  64 GB between them.

Returns: {'stdout': str, 'stderr': str, 'exit_code': int, 'timed_out': bool,
          'busy': bool, 'oom': bool}
"""

import logging
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

from game.sandbox_cgroup import RunCgroup

log = logging.getLogger('ocrcade.sandbox')

TIMEOUT         = 15   # wall-clock seconds for player code
COMPILE_TIMEOUT = 30   # wall-clock seconds for the C++ compile
SLOT_WAIT       = 5    # seconds a submission waits for the one run slot
MEMORY_MAX      = 1024 ** 3   # bytes, all of one run's processes together
PIDS_MAX        = 64
CGROUP_ENV      = 'OCRCADE_SANDBOX_CGROUP'   # '1' turns the run cgroup on
BWRAP           = '/usr/bin/bwrap'
PUZZLES_DIR     = Path(__file__).resolve().parent.parent / 'puzzles'

# Read-only host paths exposed inside the sandbox.
_RO_BINDS = ['/usr', '/lib', '/lib64', '/bin', '/sbin', '/etc/alternatives',
             '/etc/passwd', '/etc/group', '/etc/localtime']

# Applied inside the sandbox before exec. Caps: 1GB address space, 64 procs,
# 20MB file writes, 30s CPU. `exec "$@"` runs the real command as PID-equivalent.
_ULIMIT_PREAMBLE = 'ulimit -v 1048576 -u 64 -f 20480 -t 30 2>/dev/null; exec "$@"'

_SAFE_PATH = '/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin'


def _puzzle_data_dir(puzzle: int) -> Path:
    return PUZZLES_DIR / f'level{puzzle}'


_slot = threading.Lock()
_cgroup: RunCgroup | None = None
_cgroup_lock = threading.Lock()


def run_cgroup() -> RunCgroup:
    """The run cgroup, set up on first use (the server also calls this at startup so
    /health and the log report the cap before the first submission)."""
    global _cgroup
    with _cgroup_lock:
        if _cgroup is None:
            _cgroup = _make_cgroup()
        return _cgroup


def _make_cgroup() -> RunCgroup:
    # Opt-in: setup moves every process in this cgroup into server/, which on a desktop
    # is the whole terminal scope. Only ocrcade-exec.service (and the kepler test run)
    # set the variable.
    if os.environ.get(CGROUP_ENV) != '1':
        cg = RunCgroup(Path('/nonexistent'), MEMORY_MAX, PIDS_MAX)
        cg.status = f'off: {CGROUP_ENV}=1 is not set (ocrcade-exec.service sets it)'
        log.warning(f"sandbox cgroup {cg.status}; only the per-process ulimits apply")
        return cg
    try:
        cg = RunCgroup.for_this_process(MEMORY_MAX, PIDS_MAX)
    except OSError as e:  # no /proc/self/cgroup (not Linux)
        log.warning(f"sandbox cgroup unavailable: {e}")
        cg = RunCgroup(Path('/nonexistent'), MEMORY_MAX, PIDS_MAX)
    cg.setup()
    return cg


def run(puzzle: int, lang: str, code: str) -> dict:
    """Write code to a temp dir, execute in a bwrap sandbox, return result dict."""
    if not _slot.acquire(timeout=SLOT_WAIT):
        log.warning("sandbox busy: another run holds the slot")
        return _result(stderr='BUSY', exit_code=-1, busy=True)
    workdir = tempfile.mkdtemp(prefix='ocrcade_')
    try:
        return _execute(puzzle, lang, code, workdir)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
        _slot.release()


def _result(stdout: str = '', stderr: str = '', exit_code: int = 0, timed_out: bool = False,
            busy: bool = False, oom: bool = False) -> dict:
    return {'stdout': stdout, 'stderr': stderr, 'exit_code': exit_code,
            'timed_out': timed_out, 'busy': busy, 'oom': oom}


def _err(stderr: str, exit_code: int = 1) -> dict:
    return _result(stderr=stderr, exit_code=exit_code)


def _confined(cmd: list, timeout: float, cwd: str | None = None):
    """Run a sandbox command inside the run cgroup. Returns (proc or None on timeout,
    whether the run hit the memory cap). Leftover processes are killed either way."""
    cg = run_cgroup()
    cg.start()
    proc = None
    try:
        proc = subprocess.run(cg.wrap(cmd), capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL, cwd=cwd)
    except subprocess.TimeoutExpired:
        pass
    finally:
        oom = cg.finish()
    return proc, oom


def _execute(puzzle: int, lang: str, code: str, workdir: str) -> dict:
    data_dir = _puzzle_data_dir(puzzle)
    if not data_dir.exists():
        log.error(f"No puzzle data dir for puzzle {puzzle}: {data_dir}")
        return _err(f'No data for puzzle {puzzle} – contact operator')

    if lang == 'python':
        Path(workdir, 'solution.py').write_text(code)
        cmd = _build_cmd(['python3', '/sandbox/solution.py'], workdir, data_dir)

    elif lang == 'bash':
        sh = Path(workdir, 'solution.sh')
        sh.write_text('#!/bin/bash\n' + code)
        sh.chmod(0o755)
        cmd = _build_cmd(['bash', '/sandbox/solution.sh'], workdir, data_dir)

    elif lang == 'cpp':
        Path(workdir, 'solution.cpp').write_text(code)
        # Compile INSIDE the sandbox (no host fs / no #include read-oracle).
        compile_cmd = _build_cmd(
            ['g++', '-O2', '-o', '/sandbox/solution', '/sandbox/solution.cpp'],
            workdir, data_dir=None)
        cr, oom = _confined(compile_cmd, COMPILE_TIMEOUT)
        if oom:
            return _result(stderr='COMPILE MEMORY LIMIT EXCEEDED', exit_code=-1, oom=True)
        if cr is None:
            return _result(stderr='COMPILE TIME LIMIT EXCEEDED', exit_code=-1,
                           timed_out=True)
        if cr.returncode != 0:
            return _err(f'COMPILE ERROR:\n{cr.stderr}', cr.returncode)
        cmd = _build_cmd(['/sandbox/solution'], workdir, data_dir)

    else:
        return _err(f'Unsupported language: {lang}')

    log.info(f"Executing puzzle={puzzle} lang={lang}")
    proc, oom = _confined(cmd, TIMEOUT, cwd=workdir)
    if oom:
        log.warning(f"Run hit the {MEMORY_MAX // 1024 ** 2} MB memory cap")
        return _result(stdout=proc.stdout if proc else '', stderr='MEMORY LIMIT EXCEEDED',
                       exit_code=proc.returncode if proc else -1, oom=True)
    if proc is None:
        log.warning(f"Execution timed out after {TIMEOUT}s")
        return _result(stderr=f'TIME LIMIT EXCEEDED ({TIMEOUT}s)', exit_code=-1,
                       timed_out=True)
    return _result(stdout=proc.stdout, stderr=proc.stderr, exit_code=proc.returncode)


def _build_cmd(inner_cmd: list, workdir: str, data_dir: Path | None) -> list:
    """Build a hardened bwrap command wrapping inner_cmd.

    data_dir=None for the compile step (no puzzle data mounted).
    """
    cmd = [
        BWRAP,
        '--clearenv', '--setenv', 'PATH', _SAFE_PATH,
        '--unshare-user', '--uid', '65534', '--gid', '65534',
        '--unshare-net', '--unshare-pid', '--unshare-uts', '--unshare-ipc',
        '--die-with-parent', '--new-session',
    ]
    for path in _RO_BINDS:
        if os.path.exists(path):
            cmd += ['--ro-bind', path, path]
    cmd += ['--tmpfs', '/tmp', '--proc', '/proc', '--dev', '/dev',
            '--bind', workdir, '/sandbox']
    if data_dir is not None:
        cmd += ['--ro-bind', str(data_dir), '/puzzle']
    cmd += ['--chdir', '/sandbox', '--',
            '/bin/sh', '-c', _ULIMIT_PREAMBLE, 'sh'] + list(inner_cmd)
    return cmd
