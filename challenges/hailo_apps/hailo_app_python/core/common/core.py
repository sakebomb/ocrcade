"""
Minimal shim for hailo_apps.hailo_app_python.core.common.core
Provides: get_default_parser
"""
import argparse


def get_default_parser():
    parser = argparse.ArgumentParser(description='Hailo Application')
    parser.add_argument('--input', '-i', type=str, default='rpi',
                        help='Input source: rpi or usb')
    parser.add_argument('--video-width', type=int, default=1280,
                        dest='video_width')
    parser.add_argument('--video-height', type=int, default=720,
                        dest='video_height')
    parser.add_argument('--frame-rate', type=int, default=30,
                        dest='frame_rate')
    parser.add_argument('--show-fps', action='store_true', default=False)
    parser.add_argument('--disable-sync', action='store_true', default=False)
    return parser
