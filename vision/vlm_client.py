"""VLMClient — picks the right backend per read.

Code is read by an ordered list of code readers (config `vlm.code_readers`): the
first one that is reachable and does not fail answers. A reader that looks and
declines (a blurry or empty frame) is final; only a broken reader falls through.
`read_card` (the older whole-card read, used by tools/phase1_smoke.py) keeps the
primary/fallback pair. Reads are infrequent (gated, ~1 per card), so probing
availability per read is cheap.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol

from .schema import CardReading

if TYPE_CHECKING:
    import numpy as np

log = logging.getLogger("ocrcade.vlm")


class VLMBackend(Protocol):
    name: str
    def available(self) -> bool: ...
    def read_card(self, image_bgr: np.ndarray) -> CardReading: ...
    def read_code(self, image_bgr: np.ndarray) -> CardReading: ...


class VLMClient:
    def __init__(self, primary: VLMBackend | None = None,
                 fallback: VLMBackend | None = None,
                 on_unreachable: str = "degrade",
                 code_readers: Sequence[VLMBackend] | None = None):
        self.primary = primary
        self.fallback = fallback
        self.on_unreachable = on_unreachable
        self.code_readers = list(code_readers) if code_readers is not None \
            else [primary] if primary is not None else []

    def read_card(self, image_bgr: np.ndarray) -> CardReading:
        if self.primary is not None and self.primary.available():
            r = self.primary.read_card(image_bgr)
            log.info(f"[{r.backend}] readable={r.readable} puzzle={r.puzzle} "
                     f"team={r.team!r} ({r.latency_s:.1f}s)")
            return r
        log.warning("primary VLM unreachable")
        if self.on_unreachable == "degrade" and self.fallback is not None \
                and self.fallback.available():
            r = self.fallback.read_card(image_bgr)
            log.info(f"[degraded:{r.backend}] readable={r.readable} puzzle={r.puzzle}")
            return r
        return CardReading.unreadable(
            "no VLM backend reachable — connect to the GPU to read code", "none")

    def read_code(self, image_bgr: np.ndarray) -> CardReading:
        """Transcribe the code body with the first code reader that works."""
        why: list[str] = []
        for reader in self.code_readers:
            if not reader.available():
                why.append(f"{reader.name} unreachable")
                log.warning(f"code reader {reader.name} unreachable")
                continue
            r = reader.read_code(image_bgr)
            log.info(f"[{r.backend}] read_code readable={r.readable} failed={r.failed} "
                     f"({len(r.code or '')} chars, {r.latency_s:.1f}s)")
            if not r.failed:
                return r
            why.append(f"{reader.name}: {r.reason}")
        return CardReading.failure(
            f"no code reader worked ({'; '.join(why) or 'none configured'})", "none")

    @property
    def degraded(self) -> bool:
        """True when the preferred code reader is down and a later one must answer."""
        return not self.code_readers or not self.code_readers[0].available()


def _ollama(cfg: dict) -> VLMBackend:
    from .backends.ollama_vlm import OllamaVLMBackend

    o = cfg["ollama"]
    return OllamaVLMBackend(
        url=o["url"], model=o["model"], num_ctx=o.get("num_ctx", 4096),
        keep_alive=o.get("keep_alive", "60s"), timeout_s=o.get("timeout_s", 60),
    )


def _hailo_ocr(cfg: dict) -> VLMBackend:
    from .backends.hailo_ocr import HailoOCRBackend

    h = cfg["hailo_ocr"]
    return HailoOCRBackend(det_hef=h["det_hef"], rec_hef=h["rec_hef"], keys=h["keys"],
                           min_conf=h.get("min_conf", 0.6))


def _litellm(cfg: dict) -> VLMBackend:
    from .backends.litellm_vlm import LiteLLMVLMBackend

    c = cfg["litellm"]
    return LiteLLMVLMBackend(model=c["model"], url_env=c.get("url_env", "LITELLM_URL"),
                             key_env=c.get("key_env", "LITELLM_API_KEY"),
                             timeout_s=c.get("timeout_s", 30))


CODE_READERS = {"litellm": _litellm, "ollama": _ollama, "hailo_ocr": _hailo_ocr}


def from_config(cfg: dict) -> VLMClient:
    """Build a VLMClient from the parsed config['vlm'] block."""
    names = cfg.get("code_readers", ["ollama"])
    unknown = [n for n in names if n not in CODE_READERS]
    if unknown:
        raise ValueError(f"vlm.code_readers: unknown {unknown}; choose from {list(CODE_READERS)}")
    code_readers = [CODE_READERS[n](cfg) for n in names]
    primary = _ollama(cfg)
    fallback = None
    if cfg.get("fallback") == "hailo":
        try:
            from .backends.hailo_vlm import HailoVLMBackend
            h = cfg["hailo"]
            fallback = HailoVLMBackend(hef=h["hef"], max_tokens=h.get("max_tokens", 64))
        except Exception as e:  # never let an absent Hailo break the GPU path
            log.warning(f"hailo fallback unavailable: {e}")
    return VLMClient(primary, fallback, cfg.get("on_unreachable", "degrade"), code_readers)
