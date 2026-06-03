"""Hailo Qwen2-VL-2B backend — COARSE ONLY (fixed 336x336 input).

Phase 0 verdict: this HEF cannot transcribe small monospace code (it hallucinates
the body). It CAN reliably read the large header number. So its job here is:
routing (which puzzle?) when the GPU is unreachable, plus camera-challenge (5/6)
detection — which need no code body. It never claims to have read code.

Loaded lazily and kept resident (init ~10s). Only importable on kepler.
"""
from __future__ import annotations

import json
import logging
import time

import cv2
import numpy as np

from ..prompts import READ_NUMBER_PROMPT
from ..schema import CardReading

log = logging.getLogger("ocrcade.vlm.hailo")


class HailoVLMBackend:
    name = "hailo"

    def __init__(self, hef: str, max_tokens: int = 64):
        self.hef = hef
        self.max_tokens = max_tokens
        self._vdevice = None
        self._vlm = None
        self._shape = None

    def _ensure_loaded(self) -> bool:
        if self._vlm is not None:
            return True
        try:
            from hailo_platform import VDevice
            from hailo_platform.genai import VLM
        except ImportError as e:
            log.warning(f"hailo_platform unavailable (not on kepler?): {e}")
            return False
        try:
            t0 = time.time()
            self._vdevice = VDevice()
            self._vlm = VLM(self._vdevice, self.hef, False)
            self._shape = tuple(self._vlm.input_frame_shape())  # (h, w, c)
            log.info(f"Hailo VLM loaded in {time.time()-t0:.1f}s, frame={self._shape}")
            return True
        except Exception as e:
            log.error(f"Hailo VLM load failed: {e}")
            return False

    def available(self) -> bool:
        return self._ensure_loaded()

    def _frame(self, image_bgr: np.ndarray) -> np.ndarray:
        h, w, _ = self._shape
        img = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
        if img.shape[0] != h or img.shape[1] != w:
            img = cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)
        return img.astype(np.uint8)

    def read_number(self, image_bgr: np.ndarray) -> int | None:
        """Read just the puzzle number — the only thing this model is trusted for."""
        if not self._ensure_loaded():
            return None
        frame = self._frame(image_bgr)
        prompt = [{"role": "user", "content": [
            {"type": "image"}, {"type": "text", "text": READ_NUMBER_PROMPT}]}]
        try:
            self._vlm.clear_context()
            out = self._vlm.generate_all(prompt=prompt, frames=[frame],
                                         temperature=0.1, seed=42,
                                         max_generated_tokens=self.max_tokens)
        except Exception as e:
            log.error(f"Hailo read_number failed: {e}")
            return None
        out = out.replace("<|im_end|>", "").strip()
        try:
            n = json.loads(out).get("puzzle")
            return int(n) if n is not None else None
        except (json.JSONDecodeError, TypeError, ValueError):
            return None

    def read_card(self, image_bgr: np.ndarray) -> CardReading:
        """Coarse read: returns the puzzle number for routing but NEVER readable code.
        Camera-challenge puzzles (5/6) are marked readable since they need no body."""
        t0 = time.time()
        n = self.read_number(image_bgr)
        dt = time.time() - t0
        if n is None:
            return CardReading.unreadable("hailo: no puzzle number", self.name, dt)
        if n in (5, 6):
            # camera challenge — no code body required; team filled in by caller/daemon
            return CardReading(readable=True, puzzle=n, lang="challenge",
                               backend=self.name, latency_s=dt,
                               reason="hailo coarse: camera-challenge route")
        return CardReading(readable=False, puzzle=n, backend=self.name, latency_s=dt,
                           reason="hailo coarse: number-only, GPU needed to read code")

    def release(self) -> None:
        try:
            if self._vlm is not None:
                self._vlm.release()
            if self._vdevice is not None:
                self._vdevice.release()
        except Exception:
            pass
        self._vlm = self._vdevice = self._shape = None
