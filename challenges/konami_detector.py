#!/usr/bin/env python3
"""
Konami code detector for OCRcade Puzzle 5.

Detection: background subtraction against a player-present reference.
After the GET READY countdown the player positions themselves but does
NOT touch the controller.  A 10-frame lores reference is captured WITH
the player in place, so their shadow/presence is already baked in.
Only a finger covering a button zone causes a significant diff.

Sequence: ↑ ↑ ↓ ↓ ← → ← → ✕ □
  (D-pad first 8, then Cross / Square — both visible on the prop)
"""

import json
import logging
import os
import time
from pathlib import Path

import cv2
import numpy as np
import requests
from picamera2 import Picamera2

log = logging.getLogger('ocrcade.konami')
log.setLevel(logging.DEBUG)

# Shared secret for the server's internal endpoints (empty → server has auth off).
_OP_TOKEN = os.environ.get('OCRCADE_OPERATOR_TOKEN', '')
_OP_HEADERS = {'X-Operator-Token': _OP_TOKEN} if _OP_TOKEN else None

KONAMI_SEQUENCE = [
    'UP', 'UP', 'DOWN', 'DOWN',
    'LEFT', 'RIGHT', 'LEFT', 'RIGHT',
    'CROSS', 'SQUARE',
]

DISPLAY_SYMBOLS = {
    'UP': '↑', 'DOWN': '↓', 'LEFT': '←', 'RIGHT': '→',
    'CROSS': '✕', 'SQUARE': '□', 'CIRCLE': '○', 'TRIANGLE': '△',
}

CALIB_W, CALIB_H = 2028, 1520
LORES_W, LORES_H = 640,  480

DETECT_FPS       = 12     # frames per second during detection loop
READY_COUNTDOWN  = 3      # seconds of "get ready" before reference capture
REF_FRAMES       = 10     # lores frames to average for player-present reference

# Background-subtraction thresholds (tune here if needed)
PIXEL_THRESHOLD  = 20     # per-pixel diff to count as "changed by a finger"
COVERAGE_MIN     = 0.10   # fraction of zone pixels that must change → "pressed"

# Dwell / pacing
DWELL_FRAMES     = 8      # consecutive frames covered before press registers (~0.67s)
POST_PRESS_PAUSE = 1.2    # seconds to lock out all input after any registration


class KonamiDetector:
    def __init__(self, cam: Picamera2, exec_pi_url: str):
        self.cam          = cam
        self.submit_url   = exec_pi_url
        self.progress_url = exec_pi_url.replace('/submit', '/konami_progress')

        cfg_path = Path(__file__).parent / 'konami_config.json'
        cfg = json.loads(cfg_path.read_text())

        self.buttons      = cfg['buttons']
        self.max_attempts = cfg['max_attempts']
        self.timeout      = cfg['sequence_timeout']

        # Only monitor buttons that actually appear in the sequence
        _sequence_buttons = set(KONAMI_SEQUENCE)

        # Pre-scale all bounding boxes from calibration (1920×1080) → lores (640×480)
        sx = LORES_W / CALIB_W
        sy = LORES_H / CALIB_H
        self._scaled: dict[str, tuple[int, int, int, int]] = {}
        for name, b in self.buttons.items():
            if name not in _sequence_buttons:
                continue
            x  = max(0, int(b['x'] * sx))
            y  = max(0, int(b['y'] * sy))
            bw = max(1, int(b['w'] * sx))
            bh = max(1, int(b['h'] * sy))
            bw = min(bw, LORES_W - x)
            bh = min(bh, LORES_H - y)
            self._scaled[name] = (x, y, bw, bh)

    # ── Public ────────────────────────────────────────────────────────────────

    def run(self, team: str) -> tuple[bool, str]:
        """Block until sequence complete, timeout, or attempts exhausted."""

        # ── Phase 1: GET READY countdown ─────────────────────────────────────
        for remaining in range(READY_COUNTDOWN, 0, -1):
            log.info(f"Konami: get ready in {remaining}s…")
            self._post_progress(team, 0, self.max_attempts, 0.0,
                                ready_countdown=remaining)
            time.sleep(1.0)

        # ── Phase 2: capture reference WITH player in position ────────────────
        log.info("Konami: capturing player-present reference — do not touch controller yet")
        self._post_progress(team, 0, self.max_attempts, 0.0, ready_countdown=0)

        ref = self._capture_reference()

        # Save a debug snapshot with zones drawn for visual verification
        try:
            dbg = cv2.cvtColor(ref, cv2.COLOR_GRAY2BGR)
            for name, (x, y, w, h) in self._scaled.items():
                cv2.rectangle(dbg, (x, y), (x + w, y + h), (0, 255, 255), 2)
                cv2.putText(dbg, name, (x + 2, y + 14),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 255), 1)
            cv2.imwrite('/tmp/konami_debug_zones.jpg', dbg)
            log.info("Konami: debug zones saved to /tmp/konami_debug_zones.jpg")
        except Exception:
            pass

        log.info("Konami: reference ready — detection starting")

        # ── Phase 3: detection loop ───────────────────────────────────────────
        step             = 0
        attempts         = self.max_attempts
        start            = time.time()
        dwell            = {name: 0 for name in self._scaled}
        post_press_until = 0.0
        last_heartbeat   = 0.0

        return self._detection_loop(team, step, attempts, start,
                                    dwell, post_press_until, last_heartbeat, ref)

    def _detection_loop(self, team, step, attempts, start,
                        dwell, post_press_until, last_heartbeat, ref):
        while True:
            elapsed = time.time() - start

            if elapsed >= self.timeout:
                self._post_progress(team, step, 0, elapsed)
                return False, 'TIME UP – Konami sequence timed out'

            if attempts <= 0:
                return False, 'NO ATTEMPTS LEFT – Konami challenge failed'

            if time.time() - last_heartbeat >= 3.0:
                self._post_progress(team, step, attempts, elapsed)
                last_heartbeat = time.time()

            # Post-press lockout — ignore everything for 1.2s after any press
            if time.time() < post_press_until:
                time.sleep(1 / DETECT_FPS)
                continue

            # ── Capture + diff ────────────────────────────────────────────────
            frame     = self.cam.capture_array('lores')
            gray      = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            coverages = self._detect_covered(gray, ref)

            # Only the single most-covered button (above threshold) gets dwell.
            # All others reset — prevents an arm sweeping across multiple zones
            # from accumulating dwell on zones the finger never actually pressed.
            above    = {n: c for n, c in coverages.items() if c > COVERAGE_MIN}
            dominant = max(above, key=above.__getitem__) if above else None

            for name in self._scaled:
                if name == dominant:
                    dwell[name] += 1
                else:
                    dwell[name] = 0

            # Buttons that have reached the dwell threshold
            ready = {n: cnt for n, cnt in dwell.items()
                     if cnt >= DWELL_FRAMES}

            if ready:
                # Pick the one held longest — prevents two inputs in one frame
                registered = max(ready, key=lambda n: ready[n])
                expected   = KONAMI_SEQUENCE[step]
                sym        = DISPLAY_SYMBOLS.get(registered, registered)

                # Reset all dwells and start post-press pause
                dwell            = {name: 0 for name in self._scaled}
                post_press_until = time.time() + POST_PRESS_PAUSE

                if registered == expected:
                    step += 1
                    log.info(f"Konami ✓ {sym}  ({step}/{len(KONAMI_SEQUENCE)})")
                    self._post_progress(team, step, attempts, elapsed)
                    last_heartbeat = time.time()

                    if step == len(KONAMI_SEQUENCE):
                        log.info(f"Konami: sequence complete! team={team!r}")
                        return True, 'KONAMI CODE ACCEPTED'
                else:
                    attempts -= 1
                    exp_sym   = DISPLAY_SYMBOLS.get(expected, expected)
                    log.info(
                        f"Konami ✗ pressed {sym}, expected {exp_sym}  "
                        f"— reset (attempts left: {attempts})"
                    )
                    step = 0
                    self._post_progress(team, step, attempts, elapsed,
                                        wrong_press=registered)
                    last_heartbeat = time.time()

            time.sleep(1 / DETECT_FPS)

    # ── Internals ──────────────────────────────────────────────────────────────

    def _capture_reference(self) -> np.ndarray:
        """
        Average REF_FRAMES lores frames into a stable grayscale reference.
        Called after the GET READY countdown with the player already positioned.
        Their presence and shadows are baked into this reference, so only actual
        button presses (local foreground motion) show up in the diff.
        """
        frames = []
        for _ in range(REF_FRAMES):
            f = self.cam.capture_array('lores')
            frames.append(
                cv2.cvtColor(f, cv2.COLOR_BGR2GRAY).astype(np.float32)
            )
            time.sleep(0.08)
        return np.mean(frames, axis=0).astype(np.uint8)

    def _detect_covered(
        self, gray: np.ndarray, ref: np.ndarray
    ) -> dict[str, float]:
        """
        Return per-button coverage values (fraction of pixels changed vs reference).
        Uses absdiff so both darker (blocking) and brighter (skin reflection) presses
        are caught.
        """
        diff      = cv2.absdiff(gray, ref)
        coverages = {}

        for name, (x, y, w, h) in self._scaled.items():
            zone             = diff[y:y + h, x:x + w]
            count            = int(np.count_nonzero(zone > PIXEL_THRESHOLD))
            coverages[name]  = count / (w * h)

        log.debug("  " + "  ".join(f"{n}={v:.2f}" for n, v in coverages.items()))
        return coverages

    def _post_progress(
        self,
        team: str,
        step: int,
        attempts_left: int,
        elapsed: float,
        wrong_press: str | None = None,
        ready_countdown: int    = 0,
    ):
        try:
            requests.post(
                self.progress_url,
                json={
                    'team':            team,
                    'step':            step,
                    'attempts_left':   attempts_left,
                    'elapsed':         round(elapsed, 1),
                    'timeout':         self.timeout,
                    'wrong_press':     wrong_press,
                    'ready_countdown': ready_countdown,
                },
                timeout=2,
                headers=_OP_HEADERS,
            )
        except requests.RequestException:
            pass
