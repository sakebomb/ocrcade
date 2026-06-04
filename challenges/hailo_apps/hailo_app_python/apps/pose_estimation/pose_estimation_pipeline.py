"""
Minimal shim for hailo_apps GStreamerPoseEstimationApp.

Builds a GStreamer pipeline:
  libcamerasrc → videoconvert → hailonet (yolov8 pose) →
  hailofilter (post-process) → hailotracker → appsink (callback)

Relies on hailo-tappas-core GStreamer elements which are installed via
the hailo-tappas-core Debian package.
"""
import sys

import gi

gi.require_version('Gst', '1.0')
gi.require_version('GLib', '2.0')
from gi.repository import GLib, Gst

Gst.init(None)

HEF_PATH   = '/usr/share/hailo-models/yolov8m_pose_h10.hef'
POST_SO    = '/usr/lib/aarch64-linux-gnu/hailo/tappas/post_processes/libyolov8pose_post.so'
POST_FN    = 'yolov8pose_post'


class GStreamerPoseEstimationApp:
    """
    Builds and runs a GStreamer pipeline that feeds pose-estimated frames
    to the provided app_callback function.
    """

    def __init__(self, callback, user_data, parser):
        self.callback  = callback
        self.user_data = user_data
        args = parser.parse_args()
        self.width     = getattr(args, 'video_width',  1280)
        self.height    = getattr(args, 'video_height',  720)
        self.fps       = getattr(args, 'frame_rate',    30)
        self.src_type  = getattr(args, 'input',         'rpi')
        self.loop      = GLib.MainLoop()
        self.pipeline  = None

    def _build_pipeline(self) -> str:
        w, h, fps = self.width, self.height, self.fps

        if self.src_type == 'rpi':
            src = (
                f'libcamerasrc auto-focus-mode=2 ! '
                f'video/x-raw,format=RGB,width={w},height={h},framerate={fps}/1'
            )
        else:
            src = (
                f'v4l2src device=/dev/video0 ! '
                f'video/x-raw,format=YUY2,width={w},height={h},framerate={fps}/1 ! '
                f'videoconvert ! video/x-raw,format=RGB'
            )

        pipeline_str = (
            f'{src} ! '
            f'queue leaky=downstream max-size-buffers=3 max-size-bytes=0 max-size-time=0 ! '
            f'videoconvert ! video/x-raw,format=RGB,width={w},height={h} ! '
            f'hailonet hef-path={HEF_PATH} is-active=true batch-size=1 ! '
            f'queue leaky=downstream max-size-buffers=3 max-size-bytes=0 max-size-time=0 ! '
            f'hailofilter so-path={POST_SO} function-name={POST_FN} qos=false ! '
            f'queue leaky=downstream max-size-buffers=3 max-size-bytes=0 max-size-time=0 ! '
            f'hailotracker keep-past-metadata=true qos=false ! '
            f'queue leaky=downstream max-size-buffers=3 max-size-bytes=0 max-size-time=0 ! '
            f'appsink name=hailo_sink emit-signals=true sync=false max-buffers=1 drop=true'
        )
        return pipeline_str

    def run(self):
        pipeline_str = self._build_pipeline()
        print(f'[hailo_shim] Pipeline:\n{pipeline_str}\n')

        self.pipeline = Gst.parse_launch(pipeline_str)
        if not self.pipeline:
            print('[hailo_shim] ERROR: Failed to create pipeline', file=sys.stderr)
            return

        sink = self.pipeline.get_by_name('hailo_sink')
        if sink is None:
            print('[hailo_shim] ERROR: appsink not found', file=sys.stderr)
            return

        sink.connect('new-sample', self._on_new_sample)

        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect('message', self._on_bus_message)

        self.pipeline.set_state(Gst.State.PLAYING)

        try:
            self.loop.run()
        except KeyboardInterrupt:
            pass
        finally:
            self.pipeline.set_state(Gst.State.NULL)

    def _on_new_sample(self, sink):
        sample = sink.emit('pull-sample')
        if sample is None:
            return Gst.FlowReturn.ERROR

        buf  = sample.get_buffer()
        pad  = sink.get_static_pad('sink')

        self.callback(pad, type('Info', (), {'get_buffer': lambda s: buf})(), self.user_data)

        return Gst.FlowReturn.OK

    def _on_bus_message(self, bus, message):
        t = message.type
        if t == Gst.MessageType.EOS:
            print('[hailo_shim] EOS received')
            self.loop.quit()
        elif t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            print(f'[hailo_shim] Pipeline error: {err.message}', file=sys.stderr)
            print(f'[hailo_shim] Debug: {debug}', file=sys.stderr)
            self.loop.quit()

    def stop(self):
        if self.loop.is_running():
            self.loop.quit()
