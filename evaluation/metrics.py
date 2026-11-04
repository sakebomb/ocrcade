"""How close a read is to the code that was actually written.

Comparison runs on normalized text: the sandbox does not care about trailing spaces or
blank edge lines, but it does care about indentation, so that is kept.
"""
from __future__ import annotations

TAB = "    "


def normalize(code: str) -> str:
    lines = code.replace("\r\n", "\n").replace("\t", TAB).split("\n")
    return "\n".join(line.rstrip() for line in lines).strip("\n")


def levenshtein(a: str, b: str) -> int:
    """Edit distance (insert, delete, substitute), two-row dynamic programming."""
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i]
        for j, cb in enumerate(b, start=1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def cer(truth: str, read: str | None) -> float:
    """Character error rate: edits needed per character of the truth. 1.0 when nothing
    was read; it can exceed 1.0 when the reader invents extra text."""
    t = normalize(truth)
    if not read:
        return 1.0
    return levenshtein(t, normalize(read)) / max(1, len(t))


def exact_match(truth: str, read: str | None) -> bool:
    return read is not None and normalize(truth) == normalize(read)
