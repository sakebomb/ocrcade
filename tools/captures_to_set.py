#!/usr/bin/env python3
"""Turn the daemon's saved reads into a labelled eval set. Run on kepler.

    python3 tools/captures_to_set.py ~/ocrcade-eval/event --puzzle 3

Each saved read of a code puzzle goes one of two ways:
  - `manifest.jsonl`: submitted, and its code earns the flag in the sandbox. That read is
    an exact transcript of the photo, so it is the label.
  - `review.jsonl`: everything else (a misread, a re-show, a declined read). Its `truth`
    is the reader's text, or empty when it declined. Correct `truth` to what the photo
    really says, then move the line into `manifest.jsonl`.
Images are copied in as `<date>-<name>.jpg`. Running it again only adds reads it has not
seen. Workflow: docs/EVAL.md.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import asdict
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evaluation.captures import (  # noqa: E402
    earns_flag,
    is_code_read,
    load_reads,
    to_sample,
)
from evaluation.dataset import MANIFEST, append_sample, load_samples  # noqa: E402
from vision.gating import FrameGate  # noqa: E402

REVIEW = "review.jsonl"
DEFAULT_ROOT = Path.home() / "ocrcade-captures"


def _args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Turn saved reads into a labelled eval set.")
    p.add_argument("set_dir", type=Path)
    p.add_argument("--captures", type=Path, default=DEFAULT_ROOT)
    p.add_argument("--puzzle", type=int, help="only reads of this puzzle")
    p.add_argument("--medium", default="paper", choices=("paper", "screen"))
    p.add_argument("--no-run", action="store_true",
                   help="skip the sandbox check: every read goes to review")
    return p.parse_args()


def _blur(path: Path) -> float:
    gray = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise OSError(f"cannot read {path}")
    return FrameGate.blur_score(cv2.resize(gray, (320, 240), interpolation=cv2.INTER_AREA))


def _seen(set_dir: Path) -> set[str]:
    names = {s.image for s in load_samples(set_dir)}
    review = set_dir / REVIEW
    if review.exists():
        names |= {json.loads(line)["image"] for line in review.read_text().splitlines()
                  if line.strip()}
    return names


def build(set_dir: Path, captures: Path, puzzle: int | None, medium: str,
          run: bool) -> tuple[int, int]:
    """Add new reads to the set; return (labelled, for review)."""
    set_dir.mkdir(parents=True, exist_ok=True)
    seen = _seen(set_dir)
    labelled = review = 0
    for read in load_reads(captures):
        if not is_code_read(read) or read.set_image in seen:
            continue
        if puzzle is not None and read.puzzle != puzzle:
            continue
        src = captures.expanduser() / read.path
        # Decide before copying: a check that fails leaves no orphan image behind.
        verified = run and read.submitted and earns_flag(read.puzzle, read.lang, read.code)
        sample = to_sample(read, read.code or "", medium, _blur(src))
        shutil.copy2(src, set_dir / read.set_image)
        if verified:
            append_sample(set_dir, sample)
            labelled += 1
        else:
            with (set_dir / REVIEW).open("a") as f:
                f.write(json.dumps(asdict(sample)) + "\n")
            review += 1
    return labelled, review


def main() -> int:
    args = _args()
    labelled, review = build(args.set_dir, args.captures, args.puzzle, args.medium,
                             run=not args.no_run)
    print(f"{labelled} labelled -> {args.set_dir / MANIFEST}")
    print(f"{review} to review -> {args.set_dir / REVIEW} (fix `truth`, then move into "
          f"{MANIFEST})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
