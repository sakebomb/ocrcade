#!/usr/bin/env python3
"""Cut-out answer slips for the puzzles that have a fixed solution.

Each slip is only the reference program. The puzzle name sits outside the
dashed cut line, because the camera transcribes whatever is on the paper and
a heading on the slip would be submitted as code.

    python3 tools/make_answer_key.py --out puzzle_cards/answer_key.pdf

Puzzle 4 has no fixed path (the maze changes every reset). Puzzles 5 and 6
are camera games and have no program. Those three are named in the footer.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from evaluation.solutions import PUZZLES, reference  # noqa: E402

DPI = 300
PAGE_W, PAGE_H = int(8.5 * DPI), int(11 * DPI)
MARGIN = int(0.35 * DPI)
HEADER_H = int(0.85 * DPI)
FOOTER_H = int(0.7 * DPI)
GAP = int(0.15 * DPI)
LABEL_H = int(0.32 * DPI)
PAD = int(0.18 * DPI)

MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
SANS = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
SANS_REGULAR = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

NOTES = (
    "Puzzle 4 has no printed answer. The maze changes every reset.",
    "Puzzles 5 and 6 have no code. Show the marker card and play.",
)


def answer_slips() -> list[tuple[int, str]]:
    """(puzzle, program) for every reference solution, program text unchanged."""
    return [(puzzle, reference(puzzle)) for puzzle in PUZZLES]


def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def _fit_code(lines: list[str], max_w: int, max_h: int) -> tuple[ImageFont.FreeTypeFont, int]:
    for size in range(54, 16, -2):
        font = _font(MONO, size)
        widest = max(font.getlength(line) for line in lines)
        line_h = int(size * 1.4)
        if widest <= max_w and line_h * len(lines) <= max_h:
            return font, line_h
    font = _font(MONO, 16)
    return font, int(16 * 1.4)


def _dashed_rect(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int]) -> None:
    x0, y0, x1, y1 = box
    edges = ((x0, y0, x1, y0), (x1, y0, x1, y1), (x0, y1, x1, y1), (x0, y0, x0, y1))
    dash, gap = 16, 12
    for ax, ay, bx, by in edges:
        dist = max(abs(bx - ax), abs(by - ay))
        for i in range(0, dist, dash + gap):
            t0, t1 = i / dist, min(i + dash, dist) / dist
            draw.line(
                [(ax + (bx - ax) * t0, ay + (by - ay) * t0),
                 (ax + (bx - ax) * t1, ay + (by - ay) * t1)],
                fill="black", width=3)


def _draw_slip(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], puzzle: int,
               code: str, label_font: ImageFont.FreeTypeFont) -> None:
    x0, y0, x1, y1 = box
    label = f"PUZZLE {puzzle}  ·  PYTHON"
    draw.text((x0, y0 - LABEL_H + 8), label, fill="black", font=label_font)
    _dashed_rect(draw, box)
    lines = code.splitlines()
    font, line_h = _fit_code(lines, (x1 - x0) - 2 * PAD, (y1 - y0) - 2 * PAD)
    block_h = line_h * len(lines)
    y = y0 + ((y1 - y0) - block_h) // 2
    for line in lines:
        draw.text((x0 + PAD, y), line, fill="black", font=font)
        y += line_h


def _grid_boxes() -> list[tuple[int, int, int, int]]:
    top = HEADER_H
    bottom = PAGE_H - FOOTER_H
    cell_w = (PAGE_W - 2 * MARGIN - GAP) // 2
    cell_h = (bottom - top - GAP) // 2
    boxes = []
    for row in range(2):
        for col in range(2):
            x = MARGIN + col * (cell_w + GAP)
            y = top + row * (cell_h + GAP)
            boxes.append((x, y + LABEL_H, x + cell_w, y + cell_h))
    return boxes


def build_answer_page() -> Image.Image:
    page = Image.new("RGB", (PAGE_W, PAGE_H), "white")
    draw = ImageDraw.Draw(page)
    title = _font(SANS, 42)
    body = _font(SANS_REGULAR, 26)
    label = _font(SANS, 28)
    draw.text((MARGIN, 36), "ANSWER KEY", fill="black", font=title)
    draw.text((MARGIN, 96),
              "Cut on the dashed lines. Show the Puzzle N · Python marker, then hold up the slip.",
              fill="black", font=body)
    for box, (puzzle, code) in zip(_grid_boxes(), answer_slips(), strict=True):
        _draw_slip(draw, box, puzzle, code, label)
    y = PAGE_H - FOOTER_H + 24
    for note in NOTES:
        draw.text((MARGIN, y), note, fill="black", font=body)
        y += 40
    return page


def main() -> int:
    ap = argparse.ArgumentParser(description="Write the cut-out answer-key PDF.")
    ap.add_argument("--out", default="puzzle_cards/answer_key.pdf")
    args = ap.parse_args()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    build_answer_page().save(out, resolution=DPI)
    print(f"wrote {out.resolve()}  ({len(answer_slips())} slips)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
