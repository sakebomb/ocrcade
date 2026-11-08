"""What to show a player whose code ran but did not earn the flag.

A crash shows the end of its error (Python's traceback ends with the line and the
exception); a compile error shows its `error:` lines; code that ran shows what it
printed. Pure, so it unit-tests without a sandbox.
"""
from __future__ import annotations

from dataclasses import dataclass

DETAIL_LINES = 6
CRASHED = "Your code crashed. The error is below."


@dataclass(frozen=True)
class RunDetail:
    label: str                 # "ERROR" or "YOUR OUTPUT"
    lines: tuple[str, ...]


def explain(result: dict, message: str) -> tuple[str, RunDetail | None]:
    """The failure headline and the detail lines for one sandbox result."""
    stderr = result.get("stderr", "") or ""
    stdout = result.get("stdout", "") or ""
    if stderr.strip() and result.get("exit_code", 0) != 0:
        return CRASHED, RunDetail("ERROR", _error_lines(stderr))
    if stdout.strip():
        return message, RunDetail("YOUR OUTPUT", tuple(_lines(stdout)[:DETAIL_LINES]))
    return message, None


def _lines(text: str) -> list[str]:
    return [ln.rstrip() for ln in text.splitlines() if ln.strip()]


def _error_lines(stderr: str) -> tuple[str, ...]:
    lines = _lines(stderr)
    if lines and lines[0].startswith("COMPILE ERROR"):
        errors = [ln for ln in lines[1:] if "error" in ln]
        return tuple((errors or lines[1:])[:DETAIL_LINES])
    return tuple(lines[-DETAIL_LINES:])
