"""VLMClient — picks the right backend per read.

Policy (architecture locked 2026-06-03): the GPU (Ollama) is the code reader.
The Hailo is a coarse fallback for routing/camera-challenges when the GPU is
unreachable. Reads are infrequent (gated, ~1 per card) so probing availability
per read is cheap.
"""
from __future__ import annotations

import logging
from typing import Protocol

import numpy as np

from .schema import CardReading

log = logging.getLogger("ocrcade.vlm")


class VLMBackend(Protocol):
    name: str
    def available(self) -> bool: ...
    def read_card(self, image_bgr: np.ndarray) -> CardReading: ...
    def read_code(self, image_bgr: np.ndarray) -> CardReading: ...


class VLMClient:
    def __init__(self, primary: VLMBackend,
                 fallback: VLMBackend | None = None,
                 on_unreachable: str = "degrade"):
        self.primary = primary
        self.fallback = fallback
        self.on_unreachable = on_unreachable

    def read_card(self, image_bgr: np.ndarray) -> CardReading:
        if self.primary.available():
            r = self.primary.read_card(image_bgr)
            log.info(f"[{r.backend}] readable={r.readable} puzzle={r.puzzle} "
                     f"team={r.team!r} ({r.latency_s:.1f}s)")
            return r
        log.warning(f"primary VLM ({self.primary.name}) unreachable")
        if self.on_unreachable == "degrade" and self.fallback is not None \
                and self.fallback.available():
            r = self.fallback.read_card(image_bgr)
            log.info(f"[degraded:{r.backend}] readable={r.readable} puzzle={r.puzzle}")
            return r
        return CardReading.unreadable(
            "no VLM backend reachable — connect to the GPU to read code", "none")

    def read_code(self, image_bgr: np.ndarray) -> CardReading:
        """Transcribe the code body. GPU-only: the Hailo fallback can't read code
        (Phase 0), so when the primary is down we abstain rather than hallucinate."""
        if not self.primary.available():
            return CardReading.unreadable(
                "code reader (GPU) unreachable — connect to the GPU to read code", "none")
        r = self.primary.read_code(image_bgr)
        log.info(f"[{r.backend}] read_code readable={r.readable} "
                 f"({len(r.code or '')} chars, {r.latency_s:.1f}s)")
        return r

    @property
    def degraded(self) -> bool:
        """True when the code reader is down and we're on coarse fallback only."""
        return not self.primary.available()


def from_config(cfg: dict) -> VLMClient:
    """Build a VLMClient from the parsed config['vlm'] block."""
    from .backends.ollama_vlm import OllamaVLMBackend

    o = cfg["ollama"]
    primary = OllamaVLMBackend(
        url=o["url"], model=o["model"], num_ctx=o.get("num_ctx", 4096),
        keep_alive=o.get("keep_alive", "60s"), timeout_s=o.get("timeout_s", 60),
    )
    fallback = None
    if cfg.get("fallback") == "hailo":
        try:
            from .backends.hailo_vlm import HailoVLMBackend
            h = cfg["hailo"]
            fallback = HailoVLMBackend(hef=h["hef"], max_tokens=h.get("max_tokens", 64))
        except Exception as e:  # never let an absent Hailo break the GPU path
            log.warning(f"hailo fallback unavailable: {e}")
    return VLMClient(primary, fallback, cfg.get("on_unreachable", "degrade"))
