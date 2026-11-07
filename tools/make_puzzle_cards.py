#!/usr/bin/env python3
"""Generate the printable ArUco Puzzle/Lang cards (organizer runs this once).

Produces one card per (puzzle, lang) — 21 total, puzzle 0 included — each with a big ArUco marker
plus a human-readable "PUZZLE N / LANG" label. Players show the matching card to
the camera to select their puzzle. Marker IDs follow vision.markers.default_marker_map.

    python3 tools/make_puzzle_cards.py --out puzzle_cards
    python3 tools/make_puzzle_cards.py --out puzzle_cards --sheet   # + a contact sheet

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


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate ArUco puzzle/lang cards.")
    ap.add_argument("--out", default="puzzle_cards", help="output directory")
    ap.add_argument("--sheet", action="store_true", help="also write a contact sheet")
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

    print(f"\n{len(cards)} cards -> {out.resolve()}  (dict={DICT_NAME})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
