#!/usr/bin/env python3
"""Capture labelled samples for the reading eval. Run on kepler, over ssh.

    sudo systemctl stop ocrcade-vision          # it owns the camera
    python3 tools/capture_samples.py --writer alice --puzzle 2
    sudo systemctl start ocrcade-vision

Shows the reference solution to copy by hand. Each time you press Enter it grabs a short
burst and keeps the sharpest frame, saving `<set>/<name>.jpg` and a manifest line whose
truth is the reference. `--truth FILE` labels with your own code instead. Workflow and
metrics: docs/EVAL.md.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evaluation.dataset import Sample, append_sample, next_name  # noqa: E402
from evaluation.solutions import PUZZLES, reference  # noqa: E402
from vision import load_config  # noqa: E402
from vision.gating import FrameGate  # noqa: E402

DEFAULT_SET = Path.home() / "ocrcade-eval" / "handwriting"


def _args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Capture labelled samples for the reading eval.")
    p.add_argument("--writer", required=True, help="short id: a-z, 0-9, _")
    p.add_argument("--puzzle", type=int, required=True, choices=PUZZLES)
    p.add_argument("--truth", type=Path, help="label with this file instead of the reference")
    p.add_argument("--lang", default="python")
    p.add_argument("--medium", default="paper", choices=("paper", "screen"))
    p.add_argument("--set", dest="set_dir", type=Path, default=DEFAULT_SET)
    p.add_argument("--burst", type=int, default=8, help="frames per capture")
    args = p.parse_args()
    if args.burst < 1:
        p.error("--burst must be at least 1")
    return args


def _vision_service_running() -> bool:
    r = subprocess.run(["systemctl", "is-active", "--quiet", "ocrcade-vision"], check=False)
    return r.returncode == 0


def _sharpest(cam, burst: int) -> tuple:
    """Grab `burst` full-res frames and keep the sharpest (best of a hand-held burst)."""
    best, best_blur = None, -1.0
    for _ in range(burst):
        frame = cam.main()
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        blur = FrameGate.blur_score(cv2.resize(gray, (320, 240), interpolation=cv2.INTER_AREA))
        if blur > best_blur:
            best, best_blur = frame, blur
    return best, best_blur


def _capture_loop(cam, args: argparse.Namespace, truth: str, blur_min: float) -> int:
    saved = 0
    while True:
        if input("Enter = capture, q = quit > ").strip().lower() == "q":
            return saved
        frame, blur = _sharpest(cam, args.burst)
        name = next_name(args.set_dir, args.puzzle, args.writer)
        path = args.set_dir / f"{name}.jpg"
        if not cv2.imwrite(str(path), frame, [cv2.IMWRITE_JPEG_QUALITY, 95]):
            raise OSError(f"could not write {path}")  # never record a missing image
        append_sample(args.set_dir, Sample(image=f"{name}.jpg", truth=truth,
                                           puzzle=args.puzzle, lang=args.lang,
                                           writer=args.writer, medium=args.medium,
                                           blur=round(blur, 1)))
        saved += 1
        warn = f"  (below the gate's blur_min {blur_min:.0f}: hold steadier?)" \
            if blur < blur_min else ""
        print(f"saved {name}.jpg  blur={blur:.0f}{warn}")


def main() -> int:
    args = _args()
    if _vision_service_running():
        print("ocrcade-vision is running and owns the camera; stop it first:\n"
              "  sudo systemctl stop ocrcade-vision", file=sys.stderr)
        return 1
    truth = args.truth.read_text() if args.truth else reference(args.puzzle)
    args.set_dir.mkdir(parents=True, exist_ok=True)
    cfg = load_config()

    from vision.capture import Camera

    print(f"Copy this by hand (puzzle {args.puzzle}), then hold it up square to the camera:\n")
    print(truth)
    cam = Camera(cfg["camera"]["main_size"], cfg["camera"]["lores_size"],
                 cfg["camera"].get("rotate", 0))
    try:
        saved = _capture_loop(cam, args, truth, cfg["gating"]["blur_min"])
    finally:
        cam.close()
    print(f"{saved} sample(s) saved to {args.set_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
