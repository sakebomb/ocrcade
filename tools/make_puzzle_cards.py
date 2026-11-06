#!/usr/bin/env python3
"""Generate the printable ArUco Puzzle/Lang cards (organizer runs this once).

Produces one card per (puzzle, lang) — 21 total, puzzle 0 included — each with a big ArUco marker
plus a human-readable "PUZZLE N / LANG" label. Players show the matching card to
the camera to select their puzzle. Marker IDs follow vision.markers.default_marker_map.

    python3 tools/make_puzzle_cards.py --out puzzle_cards
    python3 tools/make_puzzle_cards.py --out puzzle_cards --sheet   # + a contact sheet
    python3 tools/make_puzzle_cards.py --out puzzle_cards --six     # letter pages, 6 to cut out

Requires OpenCV (present on kepler). cv2.putText keeps it dependency-free.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from vision.markers import DICT_NAME, default_marker_map  # noqa: E402

CARD_W, CARD_H = 1000, 1400          # portrait, prints cleanly on Letter/A4
MARKER_PX = 640                      # marker size on the card
QUIET = 80                           # white quiet-zone around the marker (detection needs it)
FONT = cv2.FONT_HERSHEY_SIMPLEX

# Letter, 6-up. Each cell is its own cut-out, with the marker as large as the
# cell allows. 300 dpi so a printed marker stays a couple of inches across.
DPI = 300
LETTER_W, LETTER_H = int(8.5 * DPI), int(11 * DPI)
PER_PAGE = 6
COLS, ROWS = 2, 3
OUTER = int(0.4 * DPI)
GUTTER = int(0.12 * DPI)


def _card(marker_id: int, puzzle: int, lang: str, dictionary) -> np.ndarray:
    card = np.full((CARD_H, CARD_W, 3), 255, np.uint8)
    marker = cv2.aruco.generateImageMarker(dictionary, marker_id, MARKER_PX)
    marker = cv2.cvtColor(marker, cv2.COLOR_GRAY2BGR)

    x0 = (CARD_W - MARKER_PX) // 2
    y0 = 120
    # white quiet zone (already white bg) + the marker
    card[y0:y0 + MARKER_PX, x0:x0 + MARKER_PX] = marker

    def centered(text, y, scale, thick):
        (tw, _), _ = cv2.getTextSize(text, FONT, scale, thick)
        cv2.putText(card, text, ((CARD_W - tw) // 2, y), FONT, scale, (0, 0, 0), thick, cv2.LINE_AA)

    centered(f"PUZZLE {puzzle}", y0 + MARKER_PX + 160, 3.0, 7)
    centered(lang.upper(), y0 + MARKER_PX + 300, 2.4, 6)
    centered(f"(marker {marker_id})", CARD_H - 60, 1.0, 2)
    cv2.rectangle(card, (20, 20), (CARD_W - 20, CARD_H - 20), (0, 0, 0), 3)
    return card


def _cell_size() -> tuple[int, int]:
    width = (LETTER_W - 2 * OUTER - (COLS - 1) * GUTTER) // COLS
    height = (LETTER_H - 2 * OUTER - (ROWS - 1) * GUTTER) // ROWS
    return width, height


def _scale_to_width(text: str, max_w: int, scale: float, thick: int) -> float:
    while scale > 0.5:
        (tw, _), _ = cv2.getTextSize(text, FONT, scale, thick)
        if tw <= max_w:
            return scale
        scale -= 0.05
    return scale


def _put_centered(img: np.ndarray, text: str, cx: int, baseline: int, scale: float,
                  thick: int) -> None:
    (tw, _), _ = cv2.getTextSize(text, FONT, scale, thick)
    org = (cx - tw // 2, baseline)
    cv2.putText(img, text, org, FONT, scale, (0, 0, 0), thick, cv2.LINE_AA)


def _cut_card(marker_id: int, puzzle: int, lang: str, dictionary, width: int,
              height: int) -> np.ndarray:
    """One cut-out: marker as large as the cell, puzzle and language under it."""
    card = np.full((height, width, 3), 255, np.uint8)
    quiet = int(0.16 * DPI)
    label_h = int(0.24 * height)
    cv2.rectangle(card, (8, 8), (width - 9, height - 9), (0, 0, 0), 3)
    top = 8 + quiet
    band_top = height - 8 - label_h
    side = 8 + quiet
    marker_px = max(1, min(width - 2 * side, band_top - top))
    marker = cv2.aruco.generateImageMarker(dictionary, marker_id, marker_px)
    marker = cv2.cvtColor(marker, cv2.COLOR_GRAY2BGR)
    x0 = (width - marker_px) // 2
    y0 = top + max(0, (band_top - top - marker_px) // 2)
    card[y0:y0 + marker_px, x0:x0 + marker_px] = marker
    max_w = width - 2 * quiet
    title = f"PUZZLE {puzzle}"
    s1 = _scale_to_width(title, max_w, 2.0, 4)
    s2 = _scale_to_width(lang.upper(), max_w, 1.6, 3)
    _put_centered(card, title, width // 2, band_top + int(label_h * 0.42), s1, 4)
    _put_centered(card, lang.upper(), width // 2, band_top + int(label_h * 0.82), s2, 3)
    return card


def _dashed_line(img: np.ndarray, x0: int, y0: int, x1: int, y1: int) -> None:
    dist = max(abs(x1 - x0), abs(y1 - y0))
    if dist == 0:
        return
    dash, gap = 18, 12
    for i in range(0, dist, dash + gap):
        t0 = i / dist
        t1 = min(i + dash, dist) / dist
        p0 = (int(x0 + (x1 - x0) * t0), int(y0 + (y1 - y0) * t0))
        p1 = (int(x0 + (x1 - x0) * t1), int(y0 + (y1 - y0) * t1))
        cv2.line(img, p0, p1, (0, 0, 0), 2, cv2.LINE_AA)


def _draw_cuts(page: np.ndarray, cell_w: int, cell_h: int) -> None:
    """Dashed lines on every cell edge, including the outer border."""
    xs = [OUTER + c * (cell_w + GUTTER) for c in range(COLS)]
    ys = [OUTER + r * (cell_h + GUTTER) for r in range(ROWS)]
    right = xs[-1] + cell_w
    bottom = ys[-1] + cell_h
    for x in xs:
        _dashed_line(page, x, ys[0], x, bottom)
    _dashed_line(page, right, ys[0], right, bottom)
    for y in ys:
        _dashed_line(page, xs[0], y, right, y)
    _dashed_line(page, xs[0], bottom, right, bottom)


def build_six_up_pages() -> list[np.ndarray]:
    """Letter pages, six cut-out cards each, puzzle-major order."""
    dictionary = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, DICT_NAME))
    items = sorted(default_marker_map().items())
    cell_w, cell_h = _cell_size()
    pages: list[np.ndarray] = []
    for start in range(0, len(items), PER_PAGE):
        page = np.full((LETTER_H, LETTER_W, 3), 255, np.uint8)
        for i, (mid, (puzzle, lang)) in enumerate(items[start:start + PER_PAGE]):
            x = OUTER + (i % COLS) * (cell_w + GUTTER)
            y = OUTER + (i // COLS) * (cell_h + GUTTER)
            card = _cut_card(mid, puzzle, lang, dictionary, cell_w, cell_h)
            page[y:y + cell_h, x:x + cell_w] = card
        _draw_cuts(page, cell_w, cell_h)
        pages.append(page)
    return pages


def write_six_up_pdf(pages: list[np.ndarray], path: Path) -> None:
    from PIL import Image

    images = [Image.fromarray(cv2.cvtColor(p, cv2.COLOR_BGR2RGB)) for p in pages]
    images[0].save(path, save_all=True, append_images=images[1:], resolution=DPI)


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate ArUco puzzle/lang cards.")
    ap.add_argument("--out", default="puzzle_cards", help="output directory")
    ap.add_argument("--sheet", action="store_true", help="also write a contact sheet")
    ap.add_argument("--six", action="store_true",
                    help="also write six_up.pdf, six cut-out cards per letter page")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    dictionary = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, DICT_NAME))

    cards = []
    for mid, (puzzle, lang) in sorted(default_marker_map().items()):
        card = _card(mid, puzzle, lang, dictionary)
        name = f"puzzle{puzzle}_{lang}_id{mid:02d}.png"
        cv2.imwrite(str(out / name), card)
        cards.append(card)
        print(f"wrote {name}")

    if args.sheet:
        thumbs = [cv2.resize(c, (CARD_W // 4, CARD_H // 4)) for c in cards]
        rows = [np.hstack(thumbs[i:i + 3]) for i in range(0, len(thumbs), 3)]
        sheet = np.vstack(rows)
        cv2.imwrite(str(out / "_contact_sheet.png"), sheet)
        print("wrote _contact_sheet.png")

    if args.six:
        pages = build_six_up_pages()
        pdf = out / "six_up.pdf"
        write_six_up_pdf(pages, pdf)
        print(f"wrote {pdf.name} ({len(pages)} letter pages, 6 cards each)")

    print(f"\n{len(cards)} cards -> {out.resolve()}  (dict={DICT_NAME})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
