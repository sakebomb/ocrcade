#!/usr/bin/env python3
"""
Konami controller calibration tool.

Captures a reference frame, auto-locates each button via HSV color detection,
saves bounding boxes to konami_config.json, and writes an annotated JPEG for
visual verification.

Run once before the challenge:
  python3 calibrate_konami.py

Key tuning parameters:
  CONTROLLER_ROI  — crop frame to the controller area before detection
  BUTTON_COLORS   — HSV ranges per button
  MAX_BLOB_AREA   — prevents large false-positive blobs (e.g. controller outline)
"""

import json
import logging
import sys
import time
from pathlib import Path

import cv2
import numpy as np
from picamera2 import Picamera2

LOG = logging.getLogger('calibrate_konami')
logging.basicConfig(level=logging.INFO, format='%(levelname)s  %(message)s')

CONFIG_PATH    = Path(__file__).parent / 'konami_config.json'
ANNOTATED_PATH = Path('/tmp/konami_calibration.jpg')
REFERENCE_PATH = Path('/tmp/konami_reference.jpg')

# ── ROI definitions ────────────────────────────────────────────────────────────
# Face buttons (X, Square, Triangle) live in the left half of the controller.
# D-pad lives in the right half — explicit ROI prevents mis-detecting thumbsticks.
# Circle is NOT detected by color (same blue as outline) — computed geometrically.
CONTROLLER_ROI = {'x': 80,   'y': 800, 'w': 1940, 'h': 720}  # general / face buttons (bottom half of 2028×1520)
DPAD_ROI       = {'x': 1250, 'y': 850, 'w': 700,  'h': 550}  # right-side yellow cross area

# ── Blob size limits ──────────────────────────────────────────────────────────
MIN_BLOB_AREA =   300    # ignore tiny noise specks
MAX_BLOB_AREA = 20000    # ignore large blobs (e.g. controller outline vs button)

# Padding added around detected bounding boxes (pixels, before ROI is un-cropped)
BOX_PADDING = 14

# ── HSV color ranges for each button ──────────────────────────────────────────
# CIRCLE is skipped here — it shares blue with the controller outline.
# Its position is computed geometrically from the other three face buttons.
BUTTON_COLORS = {
    'SQUARE': [
        (np.array([138, 60,  80]),  np.array([170, 255, 255])),   # pink/magenta
    ],
    'CROSS': [
        (np.array([0,   130, 80]),  np.array([8,   255, 255])),   # red low-hue
        (np.array([172, 130, 80]),  np.array([180, 255, 255])),   # red high-hue wrap
    ],
    'TRIANGLE': [
        (np.array([42,  80,  80]),  np.array([85,  255, 255])),   # green
    ],
    'DPAD': [
        (np.array([10, 100, 80]), np.array([42, 255, 255])),   # yellow-orange (broad)
    ],
}


def capture_frame() -> np.ndarray:
    LOG.info("Starting camera...")
    cam = Picamera2()
    config = cam.create_still_configuration(main={"size": (2028, 1520)})
    cam.configure(config)
    cam.start()
    time.sleep(2)
    frame = cam.capture_array()
    cam.stop()
    LOG.info("Frame captured.")
    return frame  # RGB


def find_button_bbox(
    hsv_full: np.ndarray,
    color_ranges: list,
    label: str,
    roi: dict,
    max_area: int = MAX_BLOB_AREA,
) -> dict | None:
    """
    Detect a button's bounding box using HSV color ranges within the given ROI.
    Returns absolute pixel coords {'x','y','w','h'}, or None.
    max_area overrides the global MAX_BLOB_AREA (use a larger value for the D-pad cross).
    """
    rx, ry, rw, rh = roi['x'], roi['y'], roi['w'], roi['h']
    hsv = hsv_full[ry:ry+rh, rx:rx+rw]

    mask = np.zeros(hsv.shape[:2], dtype=np.uint8)
    for lo, hi in color_ranges:
        mask |= cv2.inRange(hsv, lo, hi)

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN,  kernel)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        LOG.warning(f"  {label}: no contours found in ROI")
        return None

    # Filter by area, then pick largest passing blob
    valid = [c for c in contours
             if MIN_BLOB_AREA <= cv2.contourArea(c) <= max_area]
    if not valid:
        areas = [cv2.contourArea(c) for c in contours]
        LOG.warning(f"  {label}: no blobs in area range {MIN_BLOB_AREA}–{max_area}. "
                    f"Found areas: {[f'{a:.0f}' for a in sorted(areas, reverse=True)[:3]]}")
        return None

    largest = max(valid, key=cv2.contourArea)
    area = cv2.contourArea(largest)
    lx, ly, lw, lh = cv2.boundingRect(largest)

    # Convert back to full-frame coordinates
    fh, fw = hsv_full.shape[:2]
    ax = max(0, rx + lx - BOX_PADDING)
    ay = max(0, ry + ly - BOX_PADDING)
    aw = min(fw - ax, lw + BOX_PADDING * 2)
    ah = min(fh - ay, lh + BOX_PADDING * 2)

    LOG.info(f"  {label}: bbox ({ax},{ay}) {aw}×{ah}  area={area:.0f}")
    return {'x': int(ax), 'y': int(ay), 'w': int(aw), 'h': int(ah)}


def split_dpad(bbox: dict, frame_shape: tuple) -> dict:
    """
    Split D-pad bounding box into 4 directional arm zones (3×3 grid, edge-centers).
    """
    x, y, w, h = bbox['x'], bbox['y'], bbox['w'], bbox['h']
    fh, fw = frame_shape[:2]
    cw, ch = w // 3, h // 3

    def clamp(bx, by, bw, bh):
        bx = max(0, bx); by = max(0, by)
        bw = min(fw - bx, bw); bh = min(fh - by, bh)
        return {'x': int(bx), 'y': int(by), 'w': int(bw), 'h': int(bh)}

    return {
        'UP':    clamp(x + cw,     y,          cw, ch),
        'DOWN':  clamp(x + cw,     y + 2 * ch, cw, ch),
        'LEFT':  clamp(x,          y + ch,     cw, ch),
        'RIGHT': clamp(x + 2 * cw, y + ch,     cw, ch),
    }


def draw_annotations(frame_rgb: np.ndarray, config: dict) -> np.ndarray:
    img = cv2.cvtColor(frame_rgb.copy(), cv2.COLOR_RGB2BGR)

    # Draw the controller ROI outline
    roi = CONTROLLER_ROI
    cv2.rectangle(img,
                  (roi['x'], roi['y']),
                  (roi['x'] + roi['w'], roi['y'] + roi['h']),
                  (80, 80, 80), 2)
    cv2.putText(img, "ROI", (roi['x'] + 4, roi['y'] + 22),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (80, 80, 80), 2)

    COLORS_BGR = {
        'CIRCLE':   (255, 180,   0),
        'SQUARE':   (255,   0, 200),
        'CROSS':    (  0,   0, 255),
        'TRIANGLE': (  0, 200,   0),
        'UP':       (  0, 200, 200),
        'DOWN':     (  0, 200, 200),
        'LEFT':     (  0, 200, 200),
        'RIGHT':    (  0, 200, 200),
    }

    for name, bbox in config['buttons'].items():
        color = COLORS_BGR.get(name, (200, 200, 200))
        x, y, w, h = bbox['x'], bbox['y'], bbox['w'], bbox['h']
        cv2.rectangle(img, (x, y), (x + w, y + h), color, 3)
        cv2.putText(img, name, (x + 4, y + 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.75, color, 2, cv2.LINE_AA)

    return img


def run():
    LOG.info("=== Konami Calibration ===")
    frame = capture_frame()

    ref_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
    cv2.imwrite(str(REFERENCE_PATH), ref_bgr)
    LOG.info(f"Reference frame saved: {REFERENCE_PATH}")

    hsv = cv2.cvtColor(frame, cv2.COLOR_RGB2HSV)

    buttons = {}
    failed  = []

    LOG.info("Detecting SQUARE, CROSS, TRIANGLE by color...")
    for name in ('SQUARE', 'CROSS', 'TRIANGLE'):
        bbox = find_button_bbox(hsv, BUTTON_COLORS[name], name, CONTROLLER_ROI)
        if bbox:
            buttons[name] = bbox
        else:
            failed.append(name)

    if failed:
        LOG.error(f"Color detection failed: {', '.join(failed)}")
        LOG.error("Adjust HSV ranges in BUTTON_COLORS and re-run.")
        sys.exit(1)

    # ── CIRCLE: geometry from the face-button diamond ─────────────────────────
    # On this controller, Triangle↔Circle and Square↔Cross are opposite pairs.
    # → Circle center = Square_center + Cross_center - Triangle_center
    def _center(b: dict) -> tuple[int, int]:
        return (b['x'] + b['w'] // 2, b['y'] + b['h'] // 2)

    avg_btn_w = (buttons['SQUARE']['w'] + buttons['CROSS']['w'] + buttons['TRIANGLE']['w']) // 3
    avg_btn_h = (buttons['SQUARE']['h'] + buttons['CROSS']['h'] + buttons['TRIANGLE']['h']) // 3
    sx, sy = _center(buttons['SQUARE'])
    cx, cy = _center(buttons['CROSS'])
    tx, ty = _center(buttons['TRIANGLE'])
    circle_cx = sx + cx - tx
    circle_cy = sy + cy - ty
    buttons['CIRCLE'] = {
        'x': int(circle_cx - avg_btn_w // 2),
        'y': int(circle_cy - avg_btn_h // 2),
        'w': int(avg_btn_w),
        'h': int(avg_btn_h),
    }
    LOG.info(f"  CIRCLE: geometry-computed center ({circle_cx},{circle_cy})  "
             f"bbox ({buttons['CIRCLE']['x']},{buttons['CIRCLE']['y']}) "
             f"{avg_btn_w}×{avg_btn_h}")

    # ── D-pad: dedicated ROI on right side of frame ───────────────────────────
    LOG.info(f"Detecting D-pad in ROI x={DPAD_ROI['x']} y={DPAD_ROI['y']} ...")
    dpad_bbox = find_button_bbox(hsv, BUTTON_COLORS['DPAD'], 'DPAD', DPAD_ROI,
                                 max_area=250_000)   # D-pad cross is large (~113k area)
    if dpad_bbox:
        directions = split_dpad(dpad_bbox, frame.shape)
        buttons.update(directions)
        LOG.info("  D-pad → UP/DOWN/LEFT/RIGHT zones defined")
    else:
        LOG.error("D-pad not found. Check DPAD_ROI covers the yellow cross area.")
        sys.exit(1)

    # Measure baseline color fraction for each button (no hand present).
    # Color-absence detection: "covered" when fraction drops below baseline × PRESS_RATIO.
    LOG.info("Measuring baseline color fractions...")
    DPAD_RANGES   = BUTTON_COLORS['DPAD']
    # CIRCLE uses blue ranges for color-absence detection even though its position
    # was geometry-computed (the blue zone will still lose color when covered).
    CIRCLE_RANGES = [(np.array([85, 60, 60]), np.array([130, 255, 255]))]
    button_color_ranges_raw = {
        'CIRCLE':   CIRCLE_RANGES,
        'SQUARE':   BUTTON_COLORS['SQUARE'],
        'CROSS':    BUTTON_COLORS['CROSS'],
        'TRIANGLE': BUTTON_COLORS['TRIANGLE'],
        'UP':       DPAD_RANGES,
        'DOWN':     DPAD_RANGES,
        'LEFT':     DPAD_RANGES,
        'RIGHT':    DPAD_RANGES,
    }

    PRESS_RATIO    = 0.40   # covered when color drops to < 40% of baseline
    MIN_THRESHOLD  = 0.08   # floor so buttons with sparse color still trigger

    baselines  = {}
    thresholds = {}
    for name, bbox in buttons.items():
        ranges = button_color_ranges_raw[name]
        x, y, w, h = bbox['x'], bbox['y'], bbox['w'], bbox['h']
        zone = hsv[y:y+h, x:x+w]
        mask = np.zeros(zone.shape[:2], dtype=np.uint8)
        for lo, hi in ranges:
            mask |= cv2.inRange(zone, lo, hi)
        baseline = float(np.count_nonzero(mask)) / max(w * h, 1)
        threshold = max(MIN_THRESHOLD, baseline * PRESS_RATIO)
        baselines[name]  = round(baseline, 4)
        thresholds[name] = round(threshold, 4)
        LOG.info(f"  {name}: baseline={baseline:.3f}  press_threshold={threshold:.3f}")

    # Serialize color ranges (numpy arrays → plain lists for JSON)
    serializable_ranges = {
        name: [[lo.tolist(), hi.tolist()] for lo, hi in ranges]
        for name, ranges in button_color_ranges_raw.items()
    }

    config = {
        'buttons':             buttons,
        'button_color_ranges': serializable_ranges,
        'button_baselines':    baselines,
        'button_thresholds':   thresholds,   # color fraction below this = "covered"
        'press_dwell_frames':  6,    # consecutive frames covered before registering
        'post_press_pause':    1.0,  # seconds to lock out all input after any press
        'debounce_frames':     8,    # frames must be uncovered before re-press counts
        'sequence_timeout':    90,
        'max_attempts':        3,
    }

    CONFIG_PATH.write_text(json.dumps(config, indent=2))
    LOG.info(f"Config saved: {CONFIG_PATH}")

    annotated = draw_annotations(frame, config)
    cv2.imwrite(str(ANNOTATED_PATH), annotated)
    LOG.info(f"Annotated image saved: {ANNOTATED_PATH}")

    LOG.info("=== Calibration complete ===")
    LOG.info(f"Detected: {list(buttons.keys())}")
    LOG.info("Pull /tmp/konami_calibration.jpg to verify zones.")


if __name__ == '__main__':
    run()
