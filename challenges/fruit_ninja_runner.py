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
import subprocess
import tempfile
from pathlib import Path

import requests

log = logging.getLogger('ocrcade.fruitage')

SCORE_THRESHOLD = 100   # pts required to win (≈10 fruits sliced)
GAME_TIMEOUT    = 210   # max wait: 60s game + countdown + game-over + startup slack
GAME_SCRIPT     = Path(__file__).resolve().parent / 'fruit_ninja_game.py'


def run(exec_pi_url: str) -> tuple[bool, int]:
    """
    Run the Fruit Ninja challenge.

    The caller is responsible for closing the camera before calling this and
    reopening it afterwards (see ocr_daemon._run_fruitage_challenge).

    Args:
        exec_pi_url: full submit URL, e.g. http://127.0.0.1:5000/submit

    Returns:
        (success, score)  — success is True when score >= SCORE_THRESHOLD
    """
    base_url = exec_pi_url.replace('/submit', '')
    result_path = Path(tempfile.mktemp(suffix='_fn_result.json'))
    score = 0

    # Pause the OCRcade display — shows "FRUIT NINJA CHALLENGE" then freezes fb0
    try:
        requests.post(f'{base_url}/fruitage_pause', timeout=5)
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
        log.info(f"Fruit Ninja exited with code {proc.returncode}")

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
            requests.post(f'{base_url}/fruitage_resume', timeout=5)
            log.info("Display resumed after Fruit Ninja")
        except requests.RequestException as e:
            log.warning(f"Could not resume display: {e}")

    return score >= SCORE_THRESHOLD, score
