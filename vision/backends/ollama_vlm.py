"""GPU VLM backend: Qwen2.5-VL via Ollama. The real code reader.

Phase 0: on-GPU at num_ctx=4096 (~5-8s/read), character-perfect on clean cards,
and honours the readable:false abstention on bad frames.
"""
from __future__ import annotations

import base64
import json
import logging
import time

import cv2
import numpy as np
import requests

from ..prompts import READ_CARD_PROMPT, READ_CODE_PROMPT
from ..schema import SUPPORTED_LANGS, CardReading, code_reading_from_json

log = logging.getLogger("ocrcade.vlm.ollama")

_LANG_ALIASES = {"sh": "bash", "c++": "cpp", "c": "cpp", "py": "python"}


class OllamaVLMBackend:
    name = "ollama"

    def __init__(self, url: str, model: str, num_ctx: int = 4096,
                 keep_alive: str = "60s", timeout_s: int = 60):
        self.url = url.rstrip("/")
        self.model = model
        self.num_ctx = num_ctx
        self.keep_alive = keep_alive
        self.timeout_s = timeout_s

    def available(self) -> bool:
        try:
            r = requests.get(f"{self.url}/api/tags", timeout=3)
            return r.ok and any(
                m.get("name") == self.model for m in r.json().get("models", [])
            )
        except requests.RequestException:
            return False

    def _generate(self, prompt: str, image_bgr: np.ndarray) -> tuple[str | None, str, float]:
        """Run one VLM generate call. Returns (response_or_None, error, latency)."""
        ok, jpeg = cv2.imencode(".jpg", image_bgr, [cv2.IMWRITE_JPEG_QUALITY, 92])
        if not ok:
            return None, "jpeg encode failed", 0.0
        b64 = base64.b64encode(jpeg.tobytes()).decode()
        t0 = time.time()
        try:
            r = requests.post(
                f"{self.url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "images": [b64],
                    "stream": False,
                    "format": "json",
                    "keep_alive": self.keep_alive,
                    "options": {"temperature": 0, "num_ctx": self.num_ctx},
                },
                timeout=self.timeout_s,
            )
            r.raise_for_status()
        except requests.RequestException as e:
            return None, f"ollama unreachable: {e}", time.time() - t0
        return r.json().get("response", ""), "", time.time() - t0

    def read_card(self, image_bgr: np.ndarray) -> CardReading:
        resp, err, dt = self._generate(READ_CARD_PROMPT, image_bgr)
        if resp is None:
            return CardReading.unreadable(err, self.name, dt)
        return self._parse(resp, dt)

    def read_code(self, image_bgr: np.ndarray) -> CardReading:
        """Transcribe ONLY the code body (ArUco model — identity comes from the
        marker, team from a `# team:` line in the returned code)."""
        resp, err, dt = self._generate(READ_CODE_PROMPT, image_bgr)
        if resp is None:
            return CardReading.failure(err, self.name, dt)
        return code_reading_from_json(resp, self.name, dt)

    def _parse(self, resp: str, dt: float) -> CardReading:
        try:
            d = json.loads(resp)
        except json.JSONDecodeError:
            return CardReading.unreadable(f"non-JSON: {resp[:120]}", self.name, dt)
        if not d.get("readable", False):
            return CardReading.unreadable(d.get("reason", "model abstained"),
                                          self.name, dt)
        lang = str(d.get("lang", "")).strip().lower()
        lang = _LANG_ALIASES.get(lang, lang)
        puzzle = d.get("puzzle")
        try:
            puzzle = int(puzzle) if puzzle is not None else None
        except (TypeError, ValueError):
            puzzle = None
        return CardReading(
            readable=True,
            puzzle=puzzle,
            team=(str(d["team"]).strip() if d.get("team") else None),
            lang=lang if lang in SUPPORTED_LANGS else (lang or None),
            code=d.get("code"),
            backend=self.name,
            latency_s=dt,
        )
