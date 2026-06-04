#!/usr/bin/env python3
"""OCRcade vision daemon (runs on kepler).

Loop: capture lores → gate (blur/stability/content) → on a stable card, capture a
sharp frame → VLM read → route:
  - unreadable        → /feedback (honest "couldn't read", no hallucinated submit)
  - puzzle 5 (Konami) → camera challenge, then /submit token
  - puzzle 6 (Fruit Ninja) → release camera, run game, reopen, /submit token
  - complete code card → /submit {puzzle, team, lang, code}
  - incomplete        → /feedback

The gate is what keeps the VLM honest: only sharp, stable, text-bearing frames are
sent, so the model isn't handed blank/blurry frames that make it confabulate.
"""
from __future__ import annotations

import base64
import logging
import sys
import time

import cv2
import requests

from vision import load_config
from vision.capture import Camera
from vision.gating import FrameGate
from vision.vlm_client import VLMClient, from_config

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(levelname)-8s  %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger("ocrcade.daemon")

THUMB_INTERVAL = 0.5    # seconds between alignment thumbnails to the display


def _post(url: str, payload: dict, timeout: float = 5.0) -> None:
    try:
        requests.post(url, json=payload, timeout=timeout)
    except requests.RequestException as e:
        log.debug(f"POST {url} failed: {e}")


def _thumb_b64(frame_bgr) -> str:
    thumb = cv2.resize(frame_bgr, (320, 240), interpolation=cv2.INTER_AREA)
    ok, jpeg = cv2.imencode(".jpg", thumb, [cv2.IMWRITE_JPEG_QUALITY, 60])
    return base64.b64encode(jpeg.tobytes()).decode() if ok else ""


class Daemon:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.cam_cfg = cfg["camera"]
        g = cfg["gating"]
        self.scan_fps = g["scan_fps"]
        self.cooldown_s = g["cooldown_s"]
        exec_url = cfg["exec"]["url"].rstrip("/")
        self.submit_url = f"{exec_url}/submit"
        self.feedback_url = f"{exec_url}/feedback"
        self.preview_url = f"{exec_url}/preview"
        self.feed_url = f"{exec_url}/camera_feed"
        self.client: VLMClient = from_config(cfg["vlm"])
        self.gate = FrameGate(
            blur_min=g["blur_min"], stable_frames=g["stable_frames"],
            stable_diff_max=g["stable_diff_max"], min_content=g["min_content"],
        )
        self.cam = self._new_camera()

    def _new_camera(self) -> Camera:
        return Camera(main_size=self.cam_cfg["main_size"],
                      lores_size=self.cam_cfg["lores_size"],
                      rotate=self.cam_cfg.get("rotate", 0))

    def run(self) -> None:
        log.info(f"Daemon started (degraded={self.client.degraded})")
        cooldown_until = 0.0
        last_thumb = 0.0
        while True:
            now = time.time()
            if now < cooldown_until:
                time.sleep(0.2)
                continue
            lores = self.cam.lores()
            if now - last_thumb >= THUMB_INTERVAL:
                _post(self.feed_url, {"thumb": _thumb_b64(lores)}, timeout=1)
                last_thumb = now

            fire = self.gate.update(lores)
            if self.gate.last.get("has_text"):
                _post(self.preview_url, {
                    "ocr_lines": ["Hold the card steady…"],
                    "stability": self.gate.last["progress"],
                    "max_stability": 1.0, "source_type": "camera",
                }, timeout=1)

            if fire:
                try:
                    self._read_and_route()
                except Exception as exc:  # one bad read must never kill the loop
                    log.exception(f"_read_and_route crashed: {exc}")
                finally:
                    cooldown_until = time.time() + self.cooldown_s
                    self.gate.reset()

            time.sleep(1.0 / self.scan_fps)

    def _read_and_route(self) -> None:
        frame = self.cam.main()
        r = self.client.read_card(frame)

        if not r.readable:
            reason = r.reason or "Could not read the card"
            log.info(f"unreadable: {reason}")
            _post(self.feedback_url, {"error": reason,
                                      "ocr_lines": ["CARD NOT READ", reason[:48]]})
            return

        team = r.team or "UNKNOWN"

        if r.puzzle == 5:
            self._run_konami(team)
        elif r.puzzle == 6:
            self._run_fruit_ninja(team)
        elif r.is_complete_code_card():
            log.info(f"submit P{r.puzzle} team={team!r} ({len(r.code)} chars)")
            _post(self.submit_url, {"puzzle": r.puzzle, "team": team,
                                    "lang": r.lang, "code": r.code}, timeout=10)
        else:
            _post(self.feedback_url, {
                "error": "Incomplete card – need Puzzle, Team, Lang, and code",
                "ocr_lines": [f"Puzzle: {r.puzzle}", f"Team: {team}",
                              f"Lang: {r.lang}", "(code missing/unclear)"]})

    def _run_konami(self, team: str) -> None:
        from challenges.konami_detector import KonamiDetector
        log.info(f"Konami challenge: team={team!r}")
        ok, msg = KonamiDetector(self.cam.cam, self.submit_url).run(team)
        if ok:
            _post(self.submit_url, {"puzzle": 5, "team": team,
                                    "lang": "konami", "code": "KONAMI_SOLVED"}, timeout=10)
        else:
            _post(self.feedback_url, {"error": msg,
                                      "ocr_lines": ["KONAMI FAILED", f"Team: {team}"]})

    def _run_fruit_ninja(self, team: str) -> None:
        from challenges import fruit_ninja_runner
        log.info(f"Fruit Ninja challenge: team={team!r}")
        self.cam.close()  # the game opens its own GStreamer camera
        try:
            ok, score = fruit_ninja_runner.run(self.submit_url)
        finally:
            # GStreamer may hold the device briefly after the subprocess exits —
            # retry the reopen; if it truly can't reopen, re-raise so the loop guard
            # logs it rather than us silently looping on a dead camera.
            for attempt in range(3):
                try:
                    self.cam = self._new_camera()
                    break
                except Exception as e:
                    log.warning(f"Camera reopen attempt {attempt + 1}/3 failed: {e}")
                    time.sleep(1.0)
            else:
                raise RuntimeError("camera could not be reopened after Fruit Ninja")
        code = "FRUITAGE_SOLVED" if ok else f"SCORE:{score}"
        _post(self.submit_url, {"puzzle": 6, "team": team,
                                "lang": "fruitage", "code": code}, timeout=10)


def main() -> None:
    Daemon(load_config()).run()


if __name__ == "__main__":
    main()
