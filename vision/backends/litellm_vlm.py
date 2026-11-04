"""Cloud vision code reader through the homelab LiteLLM proxy (OpenAI-compatible API).

Reached over Tailscale. The URL and key come from the environment (the vision service
loads /etc/ocrcade/litellm.env); the key is never logged. A network error, timeout or
refused request (budget, rate limit) is a failure, so the client falls through to the
next code reader (the Hailo OCR on the Pi). Photos of players' code leave the Pi here.
"""
from __future__ import annotations

import base64
import logging
import os
import time

import cv2
import numpy as np
import requests

from ..prompts import READ_CODE_PROMPT
from ..schema import CardReading, code_reading_from_json

log = logging.getLogger("ocrcade.vlm.litellm")

MAX_SIDE = 1600       # px; longer frames are downscaled before upload
JPEG_QUALITY = 90
PROBE_TTL_S = 30.0    # cache the reachability probe between reads


class LiteLLMVLMBackend:
    name = "litellm"

    def __init__(self, model: str, url_env: str = "LITELLM_URL",
                 key_env: str = "LITELLM_API_KEY", timeout_s: float = 30.0):
        self.model = model
        self.url = os.environ.get(url_env, "").rstrip("/")
        self._key = os.environ.get(key_env, "")
        self._missing = [n for n, v in ((url_env, self.url), (key_env, self._key)) if not v]
        self.timeout_s = timeout_s
        self._probe: tuple[float, bool] = (0.0, False)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._key}"}

    def available(self) -> bool:
        if self._missing:
            log.warning(f"litellm not configured: {', '.join(self._missing)} unset")
            return False
        at, ok = self._probe
        if time.monotonic() - at < PROBE_TTL_S:
            return ok
        try:
            r = requests.get(f"{self.url}/v1/models", headers=self._headers(), timeout=3)
            ok = r.ok and any(m.get("id") == self.model for m in r.json().get("data", []))
        except (requests.RequestException, ValueError):
            ok = False
        self._probe = (time.monotonic(), ok)
        return ok

    def _data_uri(self, image_bgr: np.ndarray) -> str | None:
        h, w = image_bgr.shape[:2]
        s = MAX_SIDE / max(h, w)
        if s < 1:
            image_bgr = cv2.resize(image_bgr, (int(w * s), int(h * s)),
                                   interpolation=cv2.INTER_AREA)
        ok, jpeg = cv2.imencode(".jpg", image_bgr, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        if not ok:
            return None
        return "data:image/jpeg;base64," + base64.b64encode(jpeg.tobytes()).decode()

    def read_code(self, image_bgr: np.ndarray) -> CardReading:
        uri = self._data_uri(image_bgr)
        if uri is None:
            return CardReading.failure("jpeg encode failed", self.name)
        body = {
            "model": self.model,
            "temperature": 0,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": READ_CODE_PROMPT},
                {"type": "image_url", "image_url": {"url": uri}},
            ]}],
        }
        t0 = time.time()
        try:
            r = requests.post(f"{self.url}/v1/chat/completions", json=body,
                              headers=self._headers(), timeout=self.timeout_s)
            dt = time.time() - t0
            if not r.ok:  # budget/rate refusals land here; the body has no secrets
                return CardReading.failure(f"HTTP {r.status_code}: {r.text[:120]}",
                                           self.name, dt)
            content = r.json()["choices"][0]["message"]["content"] or ""
        except requests.RequestException as e:
            self._probe = (0.0, False)
            return CardReading.failure(f"litellm unreachable: {type(e).__name__}", self.name,
                                       time.time() - t0)
        except (ValueError, KeyError, IndexError, TypeError) as e:
            return CardReading.failure(f"bad response: {type(e).__name__}", self.name,
                                       time.time() - t0)
        return code_reading_from_json(content, self.name, dt)

    def read_card(self, image_bgr: np.ndarray) -> CardReading:
        return CardReading.unreadable("litellm reads code only", self.name)
