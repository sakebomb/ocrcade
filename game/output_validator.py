"""
Per-puzzle output validators.

Each validator receives the stdout string and returns (success: bool, message: str).
"""

import logging
import re
from pathlib import Path

log = logging.getLogger('ocrcade.validator')

_ROOT       = Path(__file__).resolve().parent.parent
FLAGS_DIR   = _ROOT / 'flags'
PUZZLES_DIR = _ROOT / 'puzzles'

# Runtime state for Level 4 maze (set by server.py on reset)
_current_maze: list[str] | None = None
_current_maze_solution: str | None = None


def set_level4_maze(maze_lines: list[str], solution: str):
    global _current_maze, _current_maze_solution
    _current_maze = maze_lines
    _current_maze_solution = solution


KONAMI_TOKEN   = 'KONAMI_SOLVED'
FRUITAGE_TOKEN = 'FRUITAGE_SOLVED'


def validate(puzzle: int, stdout: str) -> tuple[bool, str]:
    """
    Returns (success, message).
    If success, message is the flag string (for internal RTB submission only).
    If failure, message is the player-facing error.

    For puzzles 5 and 6, stdout is a raw token (not sandbox output) — validated
    directly without running any code.
    """
    validators = {
        0: _validate_test,
        1: _validate_level1,
        2: _validate_level2,
        3: _validate_level3,
        4: _validate_level4,
        5: _validate_level5,
        6: _validate_level6,
    }
    fn = validators.get(puzzle)
    if fn is None:
        return False, f'Unknown puzzle: {puzzle}'
    return fn(stdout.strip())


def _read_flag(n: int) -> str:
    return (FLAGS_DIR / f'flag{n}.txt').read_text().strip()


# ── Puzzle 0: Test / sanity check (0 pts, no flag needed) ────────────────────
def _validate_test(stdout: str) -> tuple[bool, str]:
    if stdout:
        return True, ''   # empty flag — RTB submission skipped by server.py
    return False, 'Your code ran but produced no output – try print("hello")'


# ── Level 1: Print flag1.txt ──────────────────────────────────────────────────
def _validate_level1(stdout: str) -> tuple[bool, str]:
    flag = _read_flag(1)
    if flag in stdout:
        return True, flag
    return False, 'Flag not found in output'


# ── Level 2: Sum numbers from data.txt ───────────────────────────────────────
# A whole number, including a decimal or exponent. The lookarounds stop a match
# inside a longer token such as 266389.0.1.
_NUMBER = re.compile(
    r'(?<![\w.])[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?(?![\w.])'
)


def _contains_number(stdout: str, expected: str) -> bool:
    """True when a printed number equals expected.

    Compare the value, do not truncate: 266389.9 is not 266389, and 266389.0 is.
    """
    if not expected:
        return False
    try:
        target = int(expected)
    except ValueError:
        return False
    for match in _NUMBER.finditer(stdout):
        try:
            value = float(match.group())
        except ValueError:
            continue
        if value == target:
            return True
    return False


def _validate_level2(stdout: str) -> tuple[bool, str]:
    expected_file = PUZZLES_DIR / 'level2' / 'expected.txt'
    expected = expected_file.read_text().strip()
    if _contains_number(stdout, expected):
        return True, _read_flag(2)
    return False, 'Expected output not found. Check your math.'


# ── Level 3: Hard-coded maze ──────────────────────────────────────────────────
def _validate_level3(stdout: str) -> tuple[bool, str]:
    maze_path = PUZZLES_DIR / 'level3' / 'maze_fixed.txt'
    maze = maze_path.read_text().splitlines()
    path = _extract_path(stdout)
    if path and _validate_maze_path(maze, path):
        return True, _read_flag(3)
    return False, 'Invalid maze path. Output a direction string (R/L/U/D) from S to E.'


# ── Level 4: Randomized maze ─────────────────────────────────────────────────
def _validate_level4(stdout: str) -> tuple[bool, str]:
    if _current_maze is None:
        return False, 'Maze not initialized – contact operator'
    path = _extract_path(stdout)
    if path and _validate_maze_path(_current_maze, path):
        return True, _read_flag(4)
    return False, 'Invalid maze path. Output a direction string (R/L/U/D) from S to E.'


# ── Level 5: Konami code (camera-based, no sandbox) ──────────────────────────
def _validate_level5(token: str) -> tuple[bool, str]:
    if token.strip() == KONAMI_TOKEN:
        return True, _read_flag(5)
    return False, 'Konami sequence not completed'


# ── Level 6: Fruit Ninja (camera-based, no sandbox) ──────────────────────────
def _validate_level6(token: str) -> tuple[bool, str]:
    if token.strip() == FRUITAGE_TOKEN:
        return True, _read_flag(6)
    return False, 'Fruit Ninja challenge not completed'


# ── Maze helpers ──────────────────────────────────────────────────────────────

def _extract_path(stdout: str) -> str | None:
    """Extract the first RLUD direction string from the program's stdout.

    The path is produced by the player's *code* (run in the sandbox), so stdout is
    clean text — no OCR-lookalike correction needed (that was a Tesseract-era hack
    that could create false matches). We just find a run of R/L/U/D (>=8 chars,
    spaces stripped) so we don't match short English words.
    """
    for candidate in re.findall(r'[A-Za-z ]{8,}', stdout):
        normalised = candidate.replace(' ', '').upper()
        if len(normalised) >= 8 and re.fullmatch(r'[RLUD]+', normalised):
            return normalised

    return None


def _validate_maze_path(maze: list[str], path: str) -> bool:
    """
    Walk the path through the maze.
    Returns True if path leads from S to E without hitting walls.
    """
    # Find start
    start = None
    end = None
    for r, row in enumerate(maze):
        for c, ch in enumerate(row):
            if ch == 'S':
                start = (r, c)
            if ch == 'E':
                end = (r, c)

    if not start or not end:
        log.error("Maze missing S or E")
        return False

    MOVES = {'R': (0, 1), 'L': (0, -1), 'U': (-1, 0), 'D': (1, 0)}
    rows = len(maze)
    cols = max(len(row) for row in maze)

    r, c = start
    for step in path:
        if step not in MOVES:
            return False
        dr, dc = MOVES[step]
        r, c = r + dr, c + dc
        if r < 0 or r >= rows or c < 0 or c >= cols:
            return False
        cell = maze[r][c] if c < len(maze[r]) else '#'
        if cell == '#':
            return False

    return (r, c) == end
