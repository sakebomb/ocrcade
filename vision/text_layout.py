"""Turn OCR pieces back into code: CTC decoding, where to cut a text line, and how to
lay recognized pieces out as lines with spaces and indentation.

Pure stdlib, so it is unit-tested in CI. The Hailo backend (backends/hailo_ocr.py) does
the image work and calls in here. Coordinates are pixels in one frame; any unit works
as long as all segments share it.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from statistics import median

INK_EMPTY = 0.5        # a column with less detector ink than this counts as blank
INDENT_STEP = 4        # code is indented in steps of 4 spaces (under 2 chars rounds to 0)
MAX_INDENT_CHARS = 18  # past 4 levels (+ slack) a "line" is a stray mark, not indented code


@dataclass(frozen=True)
class Segment:
    """One recognized piece of text and its box in the frame."""

    top: float
    bottom: float
    left: float
    right: float
    text: str

    def __post_init__(self) -> None:
        if self.bottom < self.top or self.right < self.left:
            raise ValueError(f"inverted box: {self}")

    @property
    def center_y(self) -> float:
        return (self.top + self.bottom) / 2


def ctc_decode(indices: Sequence[int], confs: Sequence[float], charset: Sequence[str],
               blank: int = 0) -> tuple[str, list[float]]:
    """Greedy CTC: collapse repeated steps, drop blanks, trim edge spaces. Returns the
    text and one confidence per character (the first step that emitted it)."""
    chars: list[str] = []
    kept: list[float] = []
    prev = None
    for i, c in zip(indices, confs, strict=True):
        if i != blank and i != prev:
            chars.append(charset[i])
            kept.append(float(c))
        prev = i
    while chars and chars[0] == " ":
        chars.pop(0)
        kept.pop(0)
    while chars and chars[-1] == " ":
        chars.pop()
        kept.pop()
    return "".join(chars), kept


def ascii_allowed(charset: Sequence[str]) -> list[bool]:
    """Which classes code may contain: the blank (index 0) and single printable ASCII
    characters. Masks a multilingual recognizer down to what a card can hold."""
    return [i == 0 or (len(c) == 1 and 32 <= ord(c) < 127) for i, c in enumerate(charset)]


def gap_cuts(ink: Sequence[float], min_gap: float) -> list[int]:
    """Cut points (column indexes) in the middle of each blank run at least `min_gap`
    wide that has ink on both sides: the gaps between words."""
    cuts: list[int] = []
    i, n = 0, len(ink)
    while i < n:
        if ink[i] >= INK_EMPTY:
            i += 1
            continue
        j = i
        while j < n and ink[j] < INK_EMPTY:
            j += 1
        if i > 0 and j < n and j - i >= min_gap:
            cuts.append((i + j) // 2)
        i = j
    return cuts


def long_cuts(ink: Sequence[float], height: float, max_aspect: float) -> list[int]:
    """Cut points that split a crop wider than `max_aspect * height` into near-equal
    pieces, each cut at the emptiest column within half a height of its target. The
    recognizer emits a bounded number of characters, so long runs must be split."""
    width = len(ink)
    pieces = -(-width // int(max_aspect * height))  # ceil; 1 piece = no cuts
    reach = max(1, int(height // 2))
    cuts: list[int] = []
    prev = 0
    for k in range(1, pieces):
        target = width * k // pieces
        lo, hi = max(prev + 1, target - reach), min(width - 1, target + reach)
        if lo > hi:
            continue
        cut = min(range(lo, hi + 1), key=lambda x: ink[x])
        cuts.append(cut)
        prev = cut
    return cuts


def group_lines(segments: Sequence[Segment]) -> list[list[Segment]]:
    """Group segments into text lines: a segment joins the first line whose vertical
    span contains its center. Lines come back top to bottom, each left to right."""
    lines: list[list[Segment]] = []  # created in order of their topmost center
    for s in sorted(segments, key=lambda s: s.center_y):
        for line in lines:
            if min(x.top for x in line) <= s.center_y <= max(x.bottom for x in line):
                line.append(s)
                break
        else:
            lines.append([s])
    return [sorted(ln, key=lambda x: x.left) for ln in lines]


def _char_width(segments: Sequence[Segment]) -> float:
    widths = [(s.right - s.left) / len(s.text) for s in segments if s.text]
    return median(widths) if widths else 0.0


def _indent(offset_chars: float) -> int:
    if offset_chars > MAX_INDENT_CHARS:
        return 0
    return INDENT_STEP * int(offset_chars / INDENT_STEP + 0.5)


def assemble(segments: Sequence[Segment]) -> str:
    """Lay segments out as code: one text line per row, words joined by a space, and
    indentation from each line's left edge measured in character widths and snapped to
    steps of four."""
    lines = group_lines(segments)
    if not lines:
        return ""
    char_w = _char_width(segments)
    base = min(ln[0].left for ln in lines)
    out = []
    for ln in lines:
        offset = (ln[0].left - base) / char_w if char_w else 0.0
        out.append(" " * _indent(offset) + " ".join(s.text for s in ln))
    return "\n".join(out)
