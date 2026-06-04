"""
Minimal shim for hailo_apps.hailo_app_python.core.gstreamer.gstreamer_app
Provides only what fruit_ninja_game.py needs: app_callback_class
"""


class app_callback_class:
    """Base class for GStreamer frame callback state."""

    def __init__(self):
        self._count = 0

    def increment(self):
        self._count += 1

    def get_count(self):
        return self._count
