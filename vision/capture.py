"""Camera wrapper (IMX500 via picamera2). kepler-only import.

Provides a sharp full-res frame for the VLM read and a cheap lores stream for the
gate. `rotate` handles a sideways mount (config-driven; the submit snaps were upright).
"""
from __future__ import annotations

import logging
import time

import cv2
import numpy as np

log = logging.getLogger("ocrcade.camera")

_ROT = {0: None, 90: cv2.ROTATE_90_CLOCKWISE,
        180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}


class Camera:
    def __init__(self, main_size=(2028, 1520), lores_size=(640, 480), rotate: int = 0):
        from picamera2 import Picamera2
        self.rotate = rotate
        self.cam = Picamera2()
        cfg = self.cam.create_still_configuration(
            main={"size": tuple(main_size)},
            lores={"size": tuple(lores_size), "format": "BGR888"},
        )
        self.cam.configure(cfg)
        self.cam.start()
        self.cam.set_controls({"AeEnable": True,
                               "FrameDurationLimits": (16667, 33333)})
        time.sleep(2)  # AE warm-up
        log.info(f"Camera started main={main_size} lores={lores_size} rotate={rotate}")

    def _rot(self, frame: np.ndarray) -> np.ndarray:
        op = _ROT.get(self.rotate)
        return cv2.rotate(frame, op) if op is not None else frame

    def lores(self) -> np.ndarray:
        return self._rot(self.cam.capture_array("lores"))

    def main(self) -> np.ndarray:
        return self._rot(self.cam.capture_array("main"))

    def close(self) -> None:
        try:
            self.cam.stop()
            self.cam.close()
        except Exception:
            pass
