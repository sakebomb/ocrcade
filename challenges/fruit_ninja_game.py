"""
Fruit Ninja main application.

Integrates Hailo pose estimation (GStreamer pipeline) with the pygame game process.
Manages inter-process communication, injects fruit detections into the video overlay,
and saves camera snapshots when a round ends.

Usage:
    python fruit_ninja_game.py --input rpi     # Raspberry Pi AI Camera
    python fruit_ninja_game.py --input usb     # USB camera
"""
import queue
import time
from pathlib import Path

import gi

gi.require_version('Gst', '1.0')
import multiprocessing as mp

import hailo
import numpy as np
from gi.repository import Gst
from hailo_apps.hailo_app_python.apps.pose_estimation.pose_estimation_pipeline import (
    GStreamerPoseEstimationApp,
)
from hailo_apps.hailo_app_python.core.common.core import get_default_parser
from hailo_apps.hailo_app_python.core.gstreamer.gstreamer_app import app_callback_class
from pygame_fruit_ninja import PygameFruitNinja

CONFIDENCE_THRESHOLD = 0.5
SNAPSHOTS_DIR = Path(__file__).parent / "snapshots"


def _save_snapshot(buffer, pad, score: int) -> None:
    """
    Extract the current video frame from the GStreamer buffer and save as JPEG.
    Falls back silently if the frame cannot be read (format mismatch, etc.).
    """
    try:
        import cv2

        caps = pad.get_current_caps()
        if not caps:
            return
        structure = caps.get_structure(0)
        width = structure.get_value("width")
        height = structure.get_value("height")
        if not (width and height):
            return

        success, map_info = buffer.map(Gst.MapFlags.READ)
        if not success:
            return

        try:
            raw = np.frombuffer(map_info.data, dtype=np.uint8)
            expected_rgb  = width * height * 3
            expected_rgba = width * height * 4

            if len(raw) == expected_rgb:
                frame = raw.reshape((height, width, 3))
                frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
            elif len(raw) == expected_rgba:
                frame = raw.reshape((height, width, 4))
                frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGBA2BGR)
            else:
                print(f"Snapshot: unexpected buffer size {len(raw)} "
                      f"(expected {expected_rgb} or {expected_rgba})")
                return

            # Burn score into the image
            label = f"Score: {score}"
            cv2.putText(frame_bgr, label, (50, height - 55),
                        cv2.FONT_HERSHEY_DUPLEX, 2.0, (0, 0, 0), 6)
            cv2.putText(frame_bgr, label, (50, height - 55),
                        cv2.FONT_HERSHEY_DUPLEX, 2.0, (255, 255, 255), 3)

            timestamp = time.strftime("%Y%m%d_%H%M%S")
            filename = SNAPSHOTS_DIR / f"fruitage_{timestamp}_score{score}.jpg"
            cv2.imwrite(str(filename), frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, 92])
            print(f"Snapshot saved: {filename}")
        finally:
            buffer.unmap(map_info)

    except Exception as e:
        print(f"Snapshot failed (non-fatal): {e}")


class user_app_callback_class(app_callback_class):
    """Callback state shared across every GStreamer frame callback invocation."""

    def __init__(self, parser) -> None:
        super().__init__()

        args = parser.parse_args()
        self.frame_width  = getattr(args, 'video_width',  1280)
        self.frame_height = getattr(args, 'video_height',  720)
        result_file = Path(args.result_file) if getattr(args, 'result_file', None) else None

        # Inter-process queues
        self.hand_positions_queue = mp.Queue(maxsize=10)
        self.fruits_queue         = mp.Queue(maxsize=100)
        self.snapshot_queue       = mp.Queue(maxsize=5)

        # Snapshot state (consumed in app_callback)
        self.pending_snapshot = None

        # Ensure snapshots directory exists
        SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)

        # Launch game process
        self.pygame_process = mp.Process(
            target=PygameFruitNinja.run_game,
            args=(
                self.hand_positions_queue,
                self.fruits_queue,
                self.snapshot_queue,
                self.frame_width,
                self.frame_height,
            ),
            kwargs={'result_file': result_file},
        )
        self.pygame_process.start()
        print(f"Fruit Ninja started — frame size: {self.frame_width}x{self.frame_height}")
        print(f"Snapshots will be saved to: {SNAPSHOTS_DIR}")

    def __del__(self) -> None:
        if hasattr(self, 'pygame_process') and self.pygame_process.is_alive():
            self.pygame_process.terminate()
            self.pygame_process.join(timeout=2)
            if self.pygame_process.is_alive():
                self.pygame_process.kill()


def app_callback(pad, info, user_data: user_app_callback_class) -> Gst.PadProbeReturn:
    user_data.increment()

    buffer = info.get_buffer()
    if buffer is None:
        return Gst.PadProbeReturn.OK

    # ── Snapshot (check every frame, save at most once per request) ──────────
    if user_data.pending_snapshot is None:
        try:
            user_data.pending_snapshot = user_data.snapshot_queue.get_nowait()
        except queue.Empty:
            pass

    if user_data.pending_snapshot is not None:
        _save_snapshot(buffer, pad, user_data.pending_snapshot['score'])
        user_data.pending_snapshot = None

    # ── Pose extraction ──────────────────────────────────────────────────────
    roi = hailo.get_roi_from_buffer(buffer)
    detections = roi.get_objects_typed(hailo.HAILO_DETECTION)

    hand_positions = {}
    for detection in detections:
        if detection.get_label() != "person":
            continue

        track_objects = detection.get_objects_typed(hailo.HAILO_UNIQUE_ID)
        if not track_objects:
            continue
        track_id = track_objects[0].get_id()

        landmark_objects = detection.get_objects_typed(hailo.HAILO_LANDMARKS)
        if not landmark_objects:
            continue
        landmarks = landmark_objects[0].get_points()

        bbox = detection.get_bbox()

        for i, keypoint_index in enumerate([9, 10]):  # left_wrist, right_wrist
            if keypoint_index >= len(landmarks):
                continue
            point = landmarks[keypoint_index]
            if (hasattr(point, 'confidence') and callable(point.confidence)
                    and point.confidence() < CONFIDENCE_THRESHOLD):
                continue

            x = int((point.x() * bbox.width() + bbox.xmin()) * user_data.frame_width)
            y = int((point.y() * bbox.height() + bbox.ymin()) * user_data.frame_height)
            hand_id = (track_id << 1) + i
            hand_positions[hand_id] = (x, y)

    try:
        if hand_positions:
            user_data.hand_positions_queue.put_nowait(hand_positions)
    except queue.Full:
        pass

    # ── Inject fruit positions as Hailo detections (video overlay) ───────────
    fruits_to_add = []
    try:
        while True:
            fruits_to_add.append(user_data.fruits_queue.get_nowait())
    except queue.Empty:
        pass

    for fruit_data in fruits_to_add:
        x, y   = fruit_data['position']
        size   = fruit_data['size']
        norm_x = x / user_data.frame_width
        norm_y = y / user_data.frame_height
        norm_w = size / user_data.frame_width
        norm_h = size / user_data.frame_height

        bbox = hailo.HailoBBox(
            max(0.0, norm_x - norm_w / 2),
            max(0.0, norm_y - norm_h / 2),
            min(1.0, norm_w),
            min(1.0, norm_h),
        )
        roi.add_object(hailo.HailoDetection(
            bbox=bbox,
            label=fruit_data['type'],
            index=fruit_data['class_id'],
            confidence=1.0,
        ))

    return Gst.PadProbeReturn.OK


if __name__ == "__main__":
    parser = get_default_parser()
    parser.set_defaults(frame_rate=30)
    parser.add_argument('--result-file', type=str, default=None,
                        help='Path to write JSON result {score: N} after game ends')

    user_data = user_app_callback_class(parser)
    app = GStreamerPoseEstimationApp(app_callback, user_data, parser)

    try:
        print("Starting Fruit Ninja…")
        app.run()
    except KeyboardInterrupt:
        print("\nShutting down…")
    finally:
        if hasattr(user_data, 'pygame_process') and user_data.pygame_process.is_alive():
            user_data.pygame_process.terminate()
            user_data.pygame_process.join(timeout=2)
            if user_data.pygame_process.is_alive():
                user_data.pygame_process.kill()
