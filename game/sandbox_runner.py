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

Returns: {'stdout': str, 'stderr': str, 'exit_code': int, 'timed_out': bool}
"""

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger('ocrcade.sandbox')

TIMEOUT         = 15   # wall-clock seconds for player code
COMPILE_TIMEOUT = 30   # wall-clock seconds for the C++ compile
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


def run(puzzle: int, lang: str, code: str) -> dict:
    """Write code to a temp dir, execute in a bwrap sandbox, return result dict."""
    workdir = tempfile.mkdtemp(prefix='ocrcade_')
    try:
        return _execute(puzzle, lang, code, workdir)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _err(stderr: str, exit_code: int = 1) -> dict:
    return {'stdout': '', 'stderr': stderr, 'exit_code': exit_code, 'timed_out': False}


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
        try:
            cr = subprocess.run(compile_cmd, capture_output=True, text=True,
                                timeout=COMPILE_TIMEOUT, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            return {'stdout': '', 'stderr': 'COMPILE TIME LIMIT EXCEEDED',
                    'exit_code': -1, 'timed_out': True}
        if cr.returncode != 0:
            return _err(f'COMPILE ERROR:\n{cr.stderr}', cr.returncode)
        cmd = _build_cmd(['/sandbox/solution'], workdir, data_dir)

    else:
        return _err(f'Unsupported language: {lang}')

    log.info(f"Executing puzzle={puzzle} lang={lang}")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT,
                              stdin=subprocess.DEVNULL, cwd=workdir)
        return {'stdout': proc.stdout, 'stderr': proc.stderr,
                'exit_code': proc.returncode, 'timed_out': False}
    except subprocess.TimeoutExpired:
        log.warning(f"Execution timed out after {TIMEOUT}s")
        return {'stdout': '', 'stderr': f'TIME LIMIT EXCEEDED ({TIMEOUT}s)',
                'exit_code': -1, 'timed_out': True}


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
