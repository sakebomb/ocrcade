"""Local frame gate — decides WHEN to spend a VLM read.

Replaces the old 15s Tesseract stability loop. Two jobs:
  1. Don't waste GPU calls on junk frames.
  2. (Phase 0 lesson) Don't feed the VLM a blurry/blank/garbage frame — that's
     exactly what makes it hallucinate a confident-but-wrong read.

A read fires only when the scene is SHARP + STABLE + has TEXT-LIKE CONTENT for
`stable_frames` consecutive frames. Cheap: runs on the grayscale lores stream.
"""
from __future__ import annotations

import logging

import cv2
import numpy as np

log = logging.getLogger("ocrcade.gate")


class FrameGate:
    def __init__(self, blur_min: float = 60.0, stable_frames: int = 4,
                 stable_diff_max: float = 8.0, min_content: float = 0.012,
                 max_content: float = 0.97):
        self.blur_min = blur_min
        self.stable_frames = stable_frames
        self.stable_diff_max = stable_diff_max
        self.min_content = min_content
        self.max_content = max_content
        self._prev: np.ndarray | None = None
        self._stable = 0
        self._armed = True
        self.last: dict = {"blur": 0.0, "diff": 0.0, "content": 0.0, "progress": 0.0}

    @staticmethod
    def _gray(frame_bgr: np.ndarray) -> np.ndarray:
        g = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        return cv2.resize(g, (320, 240), interpolation=cv2.INTER_AREA)

    @staticmethod
    def blur_score(gray: np.ndarray) -> float:
        return float(cv2.Laplacian(gray, cv2.CV_64F).var())

    @staticmethod
    def content_fraction(gray: np.ndarray) -> float:
        """Otsu-thresholded dark-pixel fraction. NOTE: the real cards are
        light text on a DARK screen photographed in a dark room, so this counts
        mostly background and runs HIGH (~0.13-0.94 on real cards, ~0.75 on an
        empty dark scene). It therefore can't tell a card from no-card — `blur`
        is the real card detector (empty ~13 vs cards ~380). This stays only as
        a loose sanity bracket [min_content, max_content] to drop degenerate
        uniform frames (a fully blank bright screen -> ~0)."""
        _, th = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
        return float(np.count_nonzero(th)) / th.size

    def update(self, frame_bgr: np.ndarray) -> bool:
        """Feed one frame. Returns True exactly once when a read should fire."""
        gray = self._gray(frame_bgr)
        blur = self.blur_score(gray)
        content = self.content_fraction(gray)

        diff = 255.0
        if self._prev is not None:
            diff = float(np.mean(cv2.absdiff(gray, self._prev)))
        self._prev = gray

        sharp = blur >= self.blur_min
        stable = diff <= self.stable_diff_max
        has_text = self.min_content <= content <= self.max_content

        if sharp and stable and has_text:
            self._stable += 1
        else:
            self._stable = 0

        self.last = {"blur": blur, "diff": diff, "content": content,
                     "progress": min(1.0, self._stable / self.stable_frames),
                     "has_text": has_text}

        if self._armed and self._stable >= self.stable_frames:
            self._armed = False
            self._stable = 0
            log.info(f"gate FIRE (blur={blur:.0f} diff={diff:.1f} content={content:.3f})")
            return True
        return False

    def reset(self) -> None:
        """Re-arm after a submission/cooldown."""
        self._stable = 0
        self._prev = None
        self._armed = True
