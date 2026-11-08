"""Saved reads (vision/captures.py) as eval samples.

The daemon saves every frame it sends to a code reader, with the read, under
`<root>/<date>/` (`reads.jsonl`). A read the player submitted that earns the flag is an
exact transcript of the photo, checked by the same sandbox and validator a submission
uses, so it becomes a labelled sample with no person involved. Every other read needs a
person to write down what the photo really says.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from vision.captures import JOURNAL

from .dataset import Sample

CODE_PUZZLES = (0, 1, 2, 3, 4)


@dataclass(frozen=True)
class CapturedRead:
    day: str              # the capture folder, YYYY-MM-DD
    image: str            # file name inside it
    puzzle: int | None
    lang: str | None
    team: str | None
    code: str | None      # what the reader returned; None when it declined
    readable: bool
    submitted: bool

    @property
    def path(self) -> str:
        return f"{self.day}/{self.image}"

    @property
    def set_image(self) -> str:
        """Its file name inside an eval set: unique across days."""
        return f"{self.day}-{self.image}"


def load_reads(root: str | Path) -> list[CapturedRead]:
    """Every saved read under root, oldest first, with whether it was submitted."""
    reads = []
    for journal in sorted(Path(root).expanduser().glob(f"*/{JOURNAL}")):
        records = [json.loads(line) for line in journal.read_text().splitlines()
                   if line.strip()]
        submitted = {r["image"] for r in records if r.get("event") == "submitted"}
        reads += [CapturedRead(day=journal.parent.name, image=r["image"],
                               puzzle=r.get("puzzle"), lang=r.get("lang"),
                               team=r.get("team"), code=r.get("code"),
                               readable=bool(r.get("readable")),
                               submitted=r["image"] in submitted)
                  for r in records if "event" not in r]
    return reads


def is_code_read(read: CapturedRead) -> bool:
    """A read of a code puzzle; the camera games (5, 6) have no code."""
    return read.puzzle in CODE_PUZZLES and read.lang is not None


def writer_token(team: str | None) -> str:
    """A Sample writer id from the team name: lowercase a-z, 0-9, _."""
    token = re.sub(r"[^a-z0-9_]+", "_", (team or "").lower()).strip("_")[:32]
    return token or "unknown"


def earns_flag(puzzle: int, lang: str, code: str | None) -> bool:
    """Would this code earn the flag? The sandbox and validator a submission goes through."""
    if not code:
        return False
    from game.output_validator import validate
    from game.sandbox_runner import run as sandbox_run

    result = sandbox_run(puzzle, lang, code)
    if result["timed_out"]:
        return False
    ok, _ = validate(puzzle, result["stdout"])
    return ok


def to_sample(read: CapturedRead, truth: str, medium: str, blur: float) -> Sample:
    return Sample(image=read.set_image, truth=truth, puzzle=read.puzzle, lang=read.lang,
                  writer=writer_token(read.team), medium=medium, blur=round(blur, 1))
