#!/usr/bin/env python3
"""OCRcade vision daemon (runs on kepler) — ArUco model.

Wires the real IMX500 camera, ArUco marker detector, blur/stability gate, GPU VLM
code reader, roster, and the exec/display server into vision.pipeline.Pipeline:

    show Puzzle·Lang ArUco card → SELECT(puzzle, lang)
    show code → gate fires → VLM reads code → roster-match the `# team:` line
              → read-back → (confirm) → /submit
    puzzles 5/6 → camera challenge (Konami / Fruit Ninja)

All the state-machine logic lives in vision/pipeline.py (unit-tested); this module
is just the concrete wiring + the camera-challenge handoff.
"""
from __future__ import annotations

import base64
import logging
import os
import sys
import time

import cv2
import requests

from vision import load_config
from vision.capture import Camera
from vision.gating import FrameGate
from vision.markers import MarkerDetector
from vision.pipeline import AutoConfirm, Pipeline
from vision.vlm_client import VLMClient, from_config

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(levelname)-8s  %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger("ocrcade.daemon")

THUMB_INTERVAL = 0.5
_OP_TOKEN = os.environ.get("OCRCADE_OPERATOR_TOKEN", "")
_OP_HEADERS = {"X-Operator-Token": _OP_TOKEN} if _OP_TOKEN else None


def _post(url: str, payload: dict, timeout: float = 5.0) -> None:
    try:
        r = requests.post(url, json=payload, timeout=timeout, headers=_OP_HEADERS)
        if r.status_code >= 400:
            hint = " (operator token mismatch?)" if r.status_code == 403 else ""
            log.warning(f"POST {url} -> {r.status_code}{hint}")
    except requests.RequestException as e:
        log.debug(f"POST {url} failed: {e}")


def _thumb_b64(frame_bgr) -> str:
    thumb = cv2.resize(frame_bgr, (320, 240), interpolation=cv2.INTER_AREA)
    ok, jpeg = cv2.imencode(".jpg", thumb, [cv2.IMWRITE_JPEG_QUALITY, 60])
    return base64.b64encode(jpeg.tobytes()).decode() if ok else ""


def _load_roster(cfg: dict) -> list[str]:
    """Registered team names: inline `names:` plus optional newline-delimited file."""
    names = list(cfg.get("names", []))
    path = cfg.get("path")
    if path:
        try:
            with open(path) as f:
                names += f.read().splitlines()
        except OSError as e:
            log.warning(f"roster file {path} unreadable: {e}")
    roster = [n.strip() for n in names if n and n.strip()]
    log.info(f"roster: {len(roster)} registered teams")
    return roster


class ServerUI:
    """Posts pipeline UI events to the exec/display server. Throttles thumbnails."""
    def __init__(self, exec_url: str):
        self.preview_url = f"{exec_url}/preview"
        self.feedback_url = f"{exec_url}/feedback"
        self.feed_url = f"{exec_url}/camera_feed"
        self._last_thumb = 0.0

    def thumb(self, frame) -> None:
        now = time.monotonic()
        if now - self._last_thumb >= THUMB_INTERVAL:
            _post(self.feed_url, {"thumb": _thumb_b64(frame)}, timeout=1)
            self._last_thumb = now

    def await_card(self) -> None:
        _post(self.preview_url, {"ocr_lines": ["Show your PUZZLE card",
                                               "(the Puzzle · Lang marker)"],
                                 "stability": 0.0, "max_stability": 1.0,
                                 "source_type": "camera"}, timeout=1)

    def puzzle_locked(self, puzzle: int, lang: str) -> None:
        _post(self.preview_url, {"ocr_lines": [f"PUZZLE {puzzle}", lang.upper(), "GOT IT"],
                                 "stability": 0.0, "max_stability": 1.0,
                                 "source_type": "puzzle_seen"}, timeout=1)

    def await_code(self, puzzle: int, lang: str, progress: float) -> None:
        lines = ["READY", "YOUR CODE"]
        if progress > 0:
            lines.append("HOLD STEADY")
        _post(self.preview_url, {"ocr_lines": lines, "stability": progress,
                                 "max_stability": 1.0, "source_type": "ready_code"}, timeout=1)

    def readback(self, puzzle: int, lang: str, team: str | None, code: str) -> None:
        lines = [f"Puzzle {puzzle} · {lang} · team {team}", "READ:"]
        lines += (code or "").splitlines()[:12]
        _post(self.preview_url, {"ocr_lines": lines, "stability": 1.0,
                                 "max_stability": 1.0, "source_type": "readback"}, timeout=2)

    def feedback(self, title: str, detail: str) -> None:
        _post(self.feedback_url, {"error": detail, "ocr_lines": [title, detail]})


class Daemon:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.cam_cfg = cfg["camera"]
        g = cfg["gating"]
        self.scan_fps = g["scan_fps"]
        exec_url = cfg["exec"]["url"].rstrip("/")
        self.submit_url = f"{exec_url}/submit"
        self.feedback_url = f"{exec_url}/feedback"
        self.client: VLMClient = from_config(cfg["vlm"])
        self.roster = _load_roster(cfg.get("roster", {}))
        self.roster_cutoff = float(cfg.get("roster", {}).get("cutoff", 0.6))

        gate = FrameGate(blur_min=g["blur_min"], stable_frames=g["stable_frames"],
                         stable_diff_max=g["stable_diff_max"], min_content=g["min_content"],
                         max_content=g.get("max_content", 0.97))
        markers = MarkerDetector(
            mapping={int(k): tuple(v) for k, v in cfg.get("markers", {}).get("map", {}).items()}
            or None,
            dict_name=cfg.get("markers", {}).get("dict", "DICT_4X4_50"))
        ui = ServerUI(exec_url)
        rb = cfg.get("readback", {})
        confirm = self._make_confirm(cfg.get("button", {}), rb)

        self.cam = self._new_camera()
        self.pipeline = Pipeline(
            markers=markers, gate=gate, reader=self.client, roster=self.roster, ui=ui,
            submit=lambda p: _post(self.submit_url, p, timeout=10), confirm=confirm,
            challenge=self._run_challenge, roster_cutoff=self.roster_cutoff,
            marker_stable_frames=int(cfg.get("select", {}).get("marker_stable_frames", 2)),
            select_timeout_s=float(cfg.get("select", {}).get("timeout_s", 60.0)),
            settle_s=float(cfg.get("select", {}).get("settle_s", 0.0)),
            clear_frames=int(cfg.get("select", {}).get("clear_frames", 1)),
            confirm_s=float(cfg.get("select", {}).get("confirm_s", 2.0)),
            readback_timeout_s=float(rb.get("timeout_s", 30.0)),
            cooldown_s=float(g.get("cooldown_s", 20.0)))

    @staticmethod
    def _make_confirm(btn: dict, rb: dict):
        """gpio → physical button (lgpio); otherwise auto-submit after a hold."""
        if btn.get("mode") == "gpio":
            from hw.button import ButtonConfirm
            return ButtonConfirm(pin=int(btn.get("gpio_pin", 17)),
                                 chip=int(btn.get("chip", 0)))
        return AutoConfirm(hold_s=float(rb.get("hold_s", 4.0)))

    def _new_camera(self) -> Camera:
        return Camera(main_size=self.cam_cfg["main_size"],
                      lores_size=self.cam_cfg["lores_size"],
                      rotate=self.cam_cfg.get("rotate", 0))

    def run(self) -> None:
        log.info(f"Daemon started (degraded={self.client.degraded}, "
                 f"roster={len(self.roster)})")
        while True:
            try:
                lores = self.cam.lores()
                self.pipeline.on_frame(lores, self.cam.main)
            except Exception as exc:  # one bad frame must never kill the loop
                log.exception(f"pipeline tick crashed: {exc}")
            time.sleep(1.0 / self.scan_fps)

    # --- camera challenges (puzzles 5/6) -----------------------------------
    # Anonymous fun rounds (user decision 2026-06-04): no team identification.
    _ANON = "ANONYMOUS"

    def _run_challenge(self, puzzle: int, lang: str) -> None:
        if puzzle == 5:
            self._run_konami(self._ANON)
        elif puzzle == 6:
            self._run_fruit_ninja(self._ANON)

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
        # The game takes the camera. Leave it alone when the game cannot start.
        if not fruit_ninja_runner._have_libcamerasrc():
            ok, score = False, None
        else:
            self.cam.close()  # the game opens its own GStreamer camera
            try:
                ok, score = fruit_ninja_runner.run(self.submit_url)
            finally:
                for attempt in range(3):
                    try:
                        self.cam = self._new_camera()
                        break
                    except Exception as e:
                        log.warning(f"Camera reopen attempt {attempt + 1}/3 failed: {e}")
                        time.sleep(1.0)
                else:
                    raise RuntimeError("camera could not be reopened after Fruit Ninja")
        kind, detail = fruit_ninja_runner.player_result(ok, score)
        if kind == "submit":
            _post(self.submit_url, {"puzzle": 6, "team": team,
                                    "lang": "fruitage", "code": detail}, timeout=10)
        else:
            _post(self.feedback_url, {"error": detail,
                                      "ocr_lines": ["FRUIT NINJA", detail]})


def main() -> None:
    Daemon(load_config()).run()


if __name__ == "__main__":
    main()
