"""Save every frame sent to a code reader, with what the reader made of it.

Each read leaves `<root>/<YYYY-MM-DD>/<HHMMSS>-p<puzzle>-<n>.jpg` and one line in that
day's `reads.jsonl`. A read the player then submits gains a second line,
`{"image": ..., "event": "submitted"}`, so confirmed reads can be told apart from
re-shows. These are photos of people's handwriting: they stay on kepler, never in git.

Saving is best effort. A full disk or a write error is logged and the game carries on.
"""
from __future__ import annotations

import json
import logging
import shutil
import time
from collections.abc import Callable
from pathlib import Path

import cv2

from .schema import CardReading

log = logging.getLogger("ocrcade.captures")

JOURNAL = "reads.jsonl"
JPEG_QUALITY = 92


class CaptureLog:
    def __init__(self, root: str | Path, min_free_mb: int = 1024,
                 clock: Callable[[], time.struct_time] = time.localtime):
        self.root = Path(root).expanduser()
        self.min_free_mb = min_free_mb
        self._clock = clock

    def save(self, image_bgr, reading: CardReading, puzzle: int | None,
             lang: str | None, team: str | None) -> str | None:
        """Write the frame and its read; return the image's path under root, or None."""
        try:
            return self._save(image_bgr, reading, puzzle, lang, team)
        except Exception as e:  # noqa: BLE001 - a capture must never break a read
            log.warning(f"capture not saved: {e}")
            return None

    def mark_submitted(self, image: str | None) -> None:
        if image is None:
            return
        try:
            self._append(self.root / Path(image).parent, {"image": Path(image).name,
                                                          "event": "submitted"})
        except OSError as e:
            log.warning(f"capture submit not recorded: {e}")

    def _save(self, image_bgr, reading, puzzle, lang, team) -> str | None:
        self.root.mkdir(parents=True, exist_ok=True)
        free_mb = shutil.disk_usage(self.root).free // (1024 * 1024)
        if free_mb < self.min_free_mb:
            log.warning(f"capture skipped: {free_mb} MB free, floor {self.min_free_mb} MB")
            return None
        now = self._clock()
        day = self.root / time.strftime("%Y-%m-%d", now)
        day.mkdir(exist_ok=True)
        name = _free_name(day, f"{time.strftime('%H%M%S', now)}-p{puzzle}")
        if not cv2.imwrite(str(day / name), image_bgr,
                           [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]):
            raise OSError(f"could not write {day / name}")
        self._append(day, {
            "image": name, "time": time.strftime("%Y-%m-%dT%H:%M:%S", now),
            "puzzle": puzzle, "lang": lang, "backend": reading.backend,
            "latency_s": round(reading.latency_s, 2), "readable": reading.readable,
            "reason": reading.reason, "team": team, "code": reading.code,
        })
        return f"{day.name}/{name}"

    @staticmethod
    def _append(day: Path, record: dict) -> None:
        with (day / JOURNAL).open("a") as f:
            f.write(json.dumps(record) + "\n")


def _free_name(day: Path, stem: str) -> str:
    n = 1
    while (day / f"{stem}-{n}.jpg").exists():
        n += 1
    return f"{stem}-{n}.jpg"
