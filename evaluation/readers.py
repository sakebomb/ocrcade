"""The code readers the eval tool can measure, by name.

A reader takes a full camera frame (BGR) and returns the code it read, or an abstention.
New strategies (line-by-line on the Hailo, preprocessing) are added here as new readers,
so every one is scored on the same labelled set. Device libraries load lazily.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

from .parse import UNREADABLE, parse_transcription

# Plain text, not JSON (see parse.py). Mirrors the production prompt's rules: copy
# exactly, never fix or complete, and abstain rather than guess.
HAILO_CODE_PROMPT = (
    "This image shows handwritten or printed source code. Transcribe it EXACTLY, "
    "character for character, keeping indentation, quotes, brackets and every symbol. "
    "Include comment lines such as '# team: NAME'. Do NOT fix, complete or reformat "
    "anything. Output ONLY the code, nothing else. If you cannot clearly read the code, "
    f"output only the word {UNREADABLE}."
)


@dataclass(frozen=True)
class ReadResult:
    code: str | None        # None = the reader abstained or failed
    reason: str             # why there is no code; empty on a read
    latency_s: float


class Reader(Protocol):
    name: str

    def read(self, image_bgr) -> ReadResult: ...


class HailoWholeFrame:
    """Qwen2-VL-2B on the Hailo-10H, whole frame squeezed to 336x336. The baseline:
    Phase 0 found it invents code at this size, and anything better must beat it."""

    name = "hailo-whole"

    def __init__(self, hef: str, max_tokens: int = 384):
        from vision.backends.hailo_vlm import HailoVLMBackend

        self._backend = HailoVLMBackend(hef=hef, max_tokens=max_tokens)

    def read(self, image_bgr) -> ReadResult:
        t0 = time.time()
        code, reason = parse_transcription(self._backend.transcribe(image_bgr, HAILO_CODE_PROMPT))
        return ReadResult(code, reason, time.time() - t0)


class OllamaCode:
    """The production GPU reader (Qwen2.5-VL-7B via Ollama), for comparison when a GPU
    is reachable. Not part of the all-on-the-Pi target."""

    name = "ollama"

    def __init__(self, cfg: dict):
        from vision.backends.ollama_vlm import OllamaVLMBackend

        self._backend = OllamaVLMBackend(
            url=cfg["url"], model=cfg["model"], num_ctx=cfg.get("num_ctx", 4096),
            keep_alive=cfg.get("keep_alive", "60s"), timeout_s=cfg.get("timeout_s", 60))

    def read(self, image_bgr) -> ReadResult:
        r = self._backend.read_code(image_bgr)
        return ReadResult(r.code if r.readable else None, "" if r.readable else r.reason,
                          r.latency_s)


READERS = ("hailo-whole", "ollama")


def make_reader(name: str, vlm_cfg: dict) -> Reader:
    """Build a reader from its name and the config's `vlm:` block."""
    if name == "hailo-whole":
        return HailoWholeFrame(hef=vlm_cfg["hailo"]["hef"])
    if name == "ollama":
        return OllamaCode(vlm_cfg["ollama"])
    raise ValueError(f"unknown reader {name!r}; choose from {', '.join(READERS)}")
