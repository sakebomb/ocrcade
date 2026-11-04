"""PaddleOCR on the Hailo-10H: read code from a camera frame, entirely on the AI HAT+ 2.

Pipeline per frame, for each candidate rotation:
  1. Text detection (PP-OCRv5 mobile) gives a text heatmap of the letterboxed frame.
  2. The heatmap is smeared sideways so the letters of a line merge; each merged blob
     is one text line, cropped upright from the full-resolution frame.
  3. Each line is cut at wide blank columns (word gaps) and, if still long, at thin
     columns, since the recognizer emits a bounded number of characters per crop.
  4. Each piece is recognized (PP-OCRv6, 18710 classes) with the output masked to
     printable ASCII, then text_layout rebuilds lines, spaces and indentation.
The rotation whose reads are most confident wins, so sideways or upside-down paper
still reads. Measured results: docs/EVAL.md. Only importable on kepler.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import cv2
import numpy as np

from ..schema import CardReading
from ..text_layout import (
    INK_EMPTY,
    Segment,
    ascii_allowed,
    assemble,
    ctc_decode,
    gap_cuts,
    join_pieces,
    long_cuts,
    parse_keys,
)

log = logging.getLogger("ocrcade.ocr.hailo")

DET_W, DET_H = 960, 544     # detector input (letterboxed)
REC_W, REC_H = 320, 48      # recognizer input (height-fit, padded right)
HEAT_ON = 0.3               # heatmap pixel counts as text above this
LINE_MEAN_MIN = 0.4         # a blob's mean heat must reach this to be a text line
LINE_MIN_AREA = 40          # detector pixels; smaller blobs are specks
LINE_MERGE = 1.2            # sideways smear, in median text heights
WORD_GAP = 0.5              # a blank run this many line heights wide splits words
PIECE_ASPECT = 5.5          # target piece width / height; a cut may shift half a height,
                            # which must still fit the recognizer's 320/48 = 6.67
PAD_Y, PAD_X = 0.35, 0.1    # crop padding, in line heights
ROTATIONS = (0, 90, 180, 270)
_ROTATE = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180,
           270: cv2.ROTATE_90_COUNTERCLOCKWISE}


@dataclass(frozen=True)
class _Letterbox:
    scale: float
    left: int
    top: int


def _letterbox(img: np.ndarray, w: int, h: int) -> tuple[np.ndarray, _Letterbox]:
    s = min(w / img.shape[1], h / img.shape[0])
    nw, nh = max(1, int(img.shape[1] * s)), max(1, int(img.shape[0] * s))
    r = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC)
    t, lft = (h - nh) // 2, (w - nw) // 2
    out = cv2.copyMakeBorder(r, t, h - nh - t, lft, w - nw - lft, cv2.BORDER_CONSTANT, value=0)
    return out, _Letterbox(s, lft, t)


def _upright(img: np.ndarray, center, size, angle: float) -> np.ndarray:
    """Crop a rotated rectangle and straighten it so its text runs left to right."""
    w, h = size
    ang = np.deg2rad(angle)
    ux = np.array([np.cos(ang), np.sin(ang)])
    uy = np.array([-np.sin(ang), np.cos(ang)])
    tl = np.array(center) - ux * w / 2 - uy * h / 2
    src = np.float32([tl, tl + ux * w, tl + ux * w + uy * h, tl + uy * h])
    W, H = max(1, int(round(w))), max(1, int(round(h)))
    dst = np.float32([[0, 0], [W, 0], [W, H], [0, H]])
    m = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(img, m, (W, H), flags=cv2.INTER_CUBIC,
                               borderMode=cv2.BORDER_REPLICATE)


def _line_axis(rect) -> tuple[tuple[float, float], float, float]:
    """A min-area rect as (size along the text, size across it), angle pointing right.
    OpenCV defines the angle only modulo 180, so it is normalized to read left to right."""
    (_, _), (w, h), a = rect
    if w < h:
        w, h, a = h, w, a + 90
    if np.cos(np.deg2rad(a)) < 0:
        a -= 180
    return (w, h), a, h


def _rec_input(crop: np.ndarray) -> np.ndarray:
    h, w = crop.shape[:2]
    nw = min(REC_W, max(1, int(round(REC_H * w / h))))
    r = cv2.resize(crop, (nw, REC_H), interpolation=cv2.INTER_LINEAR)
    return cv2.copyMakeBorder(r, 0, 0, 0, REC_W - nw, cv2.BORDER_CONSTANT, value=(128,) * 3)


class HailoOCRBackend:
    name = "hailo-ocr"

    def __init__(self, det_hef: str, rec_hef: str, keys: str, min_conf: float = 0.6,
                 rotations: tuple[int, ...] = ROTATIONS):
        self.det_hef, self.rec_hef, self.keys = det_hef, rec_hef, keys
        self.min_conf = min_conf
        self.rotations = rotations
        self._vdevice = None
        self._models: dict = {}
        self._charset: list[str] = []
        self._allow = None
        self._load_error: str | None = None

    # -- device ------------------------------------------------------------------------
    def _ensure_loaded(self) -> bool:
        """Load once. A failed load is remembered (and reported by read_text) instead of
        being retried on every frame."""
        if self._models:
            return True
        if self._load_error is not None:
            return False
        try:
            from hailo_platform import FormatType, HailoSchedulingAlgorithm, VDevice
        except ImportError as e:
            self._load_error = f"hailo_platform unavailable (not on kepler?): {e}"
            log.warning(self._load_error)
            return False
        try:
            t0 = time.time()
            with open(self.keys, encoding="utf-8", newline="") as f:
                keys_text = f.read()
            params = VDevice.create_params()
            params.scheduling_algorithm = HailoSchedulingAlgorithm.ROUND_ROBIN
            self._vdevice = VDevice(params)
            for role, hef in (("det", self.det_hef), ("rec", self.rec_hef)):
                model = self._vdevice.create_infer_model(hef)
                model.set_batch_size(1)
                model.output().set_format_type(FormatType.FLOAT32)
                ctx = model.configure()
                self._models[role] = (model, ctx, None)  # release() can close it from here
                self._models[role] = (model, ctx, ctx.__enter__())
            classes = self._models["rec"][0].output().shape[-1]
            self._charset = parse_keys(keys_text, classes)
            self._allow = np.array(ascii_allowed(self._charset))
            log.info(f"Hailo OCR loaded in {time.time()-t0:.1f}s ({classes} classes)")
            return True
        except Exception as e:
            self._load_error = f"Hailo OCR load failed: {e}"
            log.error(self._load_error)
            self.release()
            return False

    def available(self) -> bool:
        return self._ensure_loaded()

    def _infer(self, role: str, img: np.ndarray) -> np.ndarray:
        model, _, configured = self._models[role]
        out = np.empty(model.output().shape, dtype=np.float32)
        b = configured.create_bindings(output_buffers={model.output().name: out})
        b.input().set_buffer(np.ascontiguousarray(img))
        configured.run([b], 10000)
        return out

    def release(self) -> None:
        for _, ctx, _ in self._models.values():
            try:
                ctx.__exit__(None, None, None)
            except Exception as e:
                log.warning(f"Hailo OCR model release failed: {e}")
        self._models = {}
        if self._vdevice is not None:
            try:
                self._vdevice.release()
            except Exception as e:
                log.warning(f"Hailo OCR device release failed: {e}")
        self._vdevice = None

    # -- reading -----------------------------------------------------------------------
    def _recognize(self, crop: np.ndarray) -> tuple[str, list[float]]:
        p = self._infer("rec", _rec_input(crop))[0]
        p = np.where(self._allow, p, -1.0)
        return ctc_decode(p.argmax(axis=1).tolist(), p.max(axis=1).tolist(), self._charset)

    def _read_pieces(self, crop: np.ndarray, ink: np.ndarray, line_h: float):
        """Recognize one upright line crop; returns its words and their confidences."""
        sx = crop.shape[1] / max(len(ink), 1)
        edges = [0, *gap_cuts(ink.tolist(), WORD_GAP * line_h), len(ink)]
        words, confs = [], []
        for e0, e1 in zip(edges, edges[1:], strict=False):
            x0, x1 = int(e0 * sx), max(int(e1 * sx), int(e0 * sx) + 1)
            word = crop[:, x0:x1]
            word_ink = np.interp(np.linspace(e0, e1 - 1, x1 - x0), np.arange(len(ink)), ink)
            cuts = long_cuts(word_ink.tolist(), word.shape[0], PIECE_ASPECT)
            pieces = []
            for c0, c1 in zip([0, *cuts], [*cuts, word.shape[1]], strict=True):
                t, cs = self._recognize(word[:, c0:c1])
                pieces.append(t)
                confs += cs
            text = join_pieces(pieces, [word_ink[c] < INK_EMPTY for c in cuts])
            if text:
                words.append(text)
        return words, confs

    def _read_frame(self, rgb: np.ndarray) -> tuple[list[Segment], float]:
        """All confident text lines in one orientation, and their summed confidence."""
        inp, lb = _letterbox(rgb, DET_W, DET_H)
        heat = self._infer("det", inp)[:, :, 0]
        mask = (heat > HEAT_ON).astype(np.uint8)
        n, _, stats, _ = cv2.connectedComponentsWithStats(mask)
        heights = [stats[i, 3] for i in range(1, n) if stats[i, 4] >= 20]
        if not heights:
            return [], 0.0
        k = max(3, int(np.median(heights) * LINE_MERGE))
        merged = cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_RECT, (k, 1)))
        cnts, _ = cv2.findContours(merged, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        segments, score = [], 0.0
        for c in cnts:
            x, y, w, h = cv2.boundingRect(c)
            inside = mask[y:y + h, x:x + w] > 0
            if cv2.contourArea(c) < LINE_MIN_AREA or not inside.any() \
                    or heat[y:y + h, x:x + w][inside].mean() < LINE_MEAN_MIN:
                continue
            rect = cv2.minAreaRect(c)
            (lw, lh), ang, line_h = _line_axis(rect)
            px, py = PAD_X * line_h, PAD_Y * line_h
            ink = _upright(mask.astype(np.float32), rect[0], (lw + 2 * px, lh + 2 * py),
                           ang).sum(axis=0)
            center = ((rect[0][0] - lb.left) / lb.scale, (rect[0][1] - lb.top) / lb.scale)
            size = ((lw + 2 * px) / lb.scale, (lh + 2 * py) / lb.scale)
            crop = _upright(rgb, center, size, ang)
            words, confs = self._read_pieces(crop, ink, line_h)
            if not words or float(np.mean(confs)) < self.min_conf:
                continue
            # Box the text itself, not the sideways-smeared blob, so character widths
            # (and so indentation) are measured on real ink.
            cols = np.flatnonzero(inside.any(axis=0))
            segments.append(Segment(top=y, bottom=y + h, left=x + int(cols[0]),
                                    right=x + int(cols[-1]) + 1, text=" ".join(words)))
            score += float(np.sum(confs))
        return segments, score

    def read_text(self, image_bgr: np.ndarray) -> str:
        """The code in the frame, laid out as lines. An empty string means no confident
        text was found. Raises RuntimeError if the models could not be loaded."""
        if not self._ensure_loaded():
            raise RuntimeError(self._load_error or "Hailo OCR models are not loaded")
        rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
        best: tuple[list[Segment], float] = ([], 0.0)
        for rot in self.rotations:
            img = rgb if rot == 0 else cv2.rotate(rgb, _ROTATE[rot])
            segs, score = self._read_frame(img)
            if score > best[1]:
                best = (segs, score)
        return assemble(best[0])

    def read_code(self, image_bgr: np.ndarray) -> CardReading:
        t0 = time.time()
        try:
            text = self.read_text(image_bgr)
        except RuntimeError as e:  # load failure: say so, never pose as "no card"
            return CardReading.unreadable(f"hailo-ocr error: {e}", self.name, time.time() - t0)
        dt = time.time() - t0
        if not text.strip():
            return CardReading.unreadable("hailo-ocr: no confident text", self.name, dt)
        return CardReading(readable=True, code=text, backend=self.name, latency_s=dt)
