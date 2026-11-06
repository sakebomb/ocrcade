"""
Fruit Ninja challenge runner for OCRcade Puzzle 6.

Called from ocr_daemon when a Puzzle 6 card is detected.

Flow:
  1. POST /fruitage_pause  → display shows "FRUIT NINJA" screen, freezes fb0
  2. cam.close()           → releases camera device for GStreamer
  3. Launch fruit_ninja_game.py as subprocess (GStreamer + Hailo + pygame)
  4. Wait for subprocess to exit (player completes one round)
  5. Read score from result file
  6. POST /fruitage_resume → display resumes
  7. Return (success, score)
"""

import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path

import requests

log = logging.getLogger('ocrcade.fruitage')

# Shared secret for the server's internal endpoints (empty → server has auth off).
_OP_TOKEN = os.environ.get('OCRCADE_OPERATOR_TOKEN', '')
_OP_HEADERS = {'X-Operator-Token': _OP_TOKEN} if _OP_TOKEN else None

SCORE_THRESHOLD = 100   # pts required to win (≈10 fruits sliced)
GAME_TIMEOUT    = 210   # max wait: 60s game + countdown + game-over + startup slack
GAME_SCRIPT     = Path(__file__).resolve().parent / 'fruit_ninja_game.py'
SOLVED_CODE     = 'FRUITAGE_SOLVED'


def _have_libcamerasrc() -> bool:
    """The pose pipeline needs this GStreamer element. It is not part of the base OS."""
    try:
        import gi
        gi.require_version('Gst', '1.0')
        from gi.repository import Gst
        Gst.init(None)
        return Gst.ElementFactory.find('libcamerasrc') is not None
    except Exception as e:
        log.error(f"Fruit Ninja camera check failed: {e}")
        return False


def player_result(ok: bool, score: int | None) -> tuple[str, str]:
    """What the daemon should post. A crash is not a score and must not be submitted."""
    if ok:
        return 'submit', SOLVED_CODE
    if score is None:
        return 'feedback', 'Fruit Ninja did not start'
    return 'feedback', f'Score {score}. Need {SCORE_THRESHOLD}.'


def run(exec_pi_url: str) -> tuple[bool, int | None]:
    """
    Run the Fruit Ninja challenge.

    The caller is responsible for closing the camera before calling this and
    reopening it afterwards (see ocr_daemon._run_fruitage_challenge).

    Args:
        exec_pi_url: full submit URL, e.g. http://127.0.0.1:5000/submit

    Returns:
        (success, score). score is None when the game never finished. success is
        True only when a finished game scored at least SCORE_THRESHOLD.
    """
    if not _have_libcamerasrc():
        log.error("Fruit Ninja needs GStreamer element libcamerasrc "
                  "(package gstreamer1.0-libcamera)")
        return False, None

    base_url = exec_pi_url.replace('/submit', '')
    result_path = Path(tempfile.mktemp(suffix='_fn_result.json'))
    score: int | None = None
    exit_code: int | None = None

    # Pause the OCRcade display — shows "FRUIT NINJA CHALLENGE" then freezes fb0
    try:
        requests.post(f'{base_url}/fruitage_pause', timeout=5, headers=_OP_HEADERS)
        log.info("Display paused for Fruit Ninja")
    except requests.RequestException as e:
        log.warning(f"Could not pause display: {e}")

    try:
        log.info(f"Launching Fruit Ninja — result file: {result_path}")
        proc = subprocess.run(
            ['python3', str(GAME_SCRIPT), '--input', 'rpi',
             '--result-file', str(result_path)],
            timeout=GAME_TIMEOUT,
        )
        exit_code = proc.returncode
        log.info(f"Fruit Ninja exited with code {exit_code}")

        if result_path.exists():
            data = json.loads(result_path.read_text())
            score = int(data.get('score', 0))
            log.info(f"Fruit Ninja final score: {score}")
        else:
            log.warning("No result file found — game may have crashed before finishing")

    except subprocess.TimeoutExpired:
        log.error(f"Fruit Ninja timed out after {GAME_TIMEOUT}s")
    except Exception as e:
        log.error(f"Fruit Ninja runner error: {e}")
    finally:
        result_path.unlink(missing_ok=True)

        # Resume OCRcade display
        try:
            requests.post(f'{base_url}/fruitage_resume', timeout=5, headers=_OP_HEADERS)
            log.info("Display resumed after Fruit Ninja")
        except requests.RequestException as e:
            log.warning(f"Could not resume display: {e}")

    if exit_code != 0 or score is None:
        return False, None
    return score >= SCORE_THRESHOLD, score
