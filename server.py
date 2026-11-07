#!/usr/bin/env python3
"""OCRcade exec/display server.

Receives code submissions (from daemon.py), runs them in the sandbox, validates
output, submits flags to RootTheBox, and drives the display. Pure exec side — it
does no card reading; that's the vision daemon's job.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import sys
import threading
import time
from collections.abc import Callable
from functools import wraps
from pathlib import Path

from flask import Flask, jsonify, request

from display.display_manager import Display
from game.maze_generator import generate_maze
from game.output_validator import set_level4_maze, validate
from game.rtb_submitter import is_solved
from game.rtb_submitter import submit as rtb_submit
from game.sandbox_runner import run as sandbox_run
from game.sandbox_runner import run_cgroup as sandbox_cgroup
from vision import load_config

ROOT        = Path(__file__).resolve().parent
PUZZLES_DIR = ROOT / "puzzles"
SOLVED_LOG  = ROOT / "solved_log.json"

PUZZLE_NAMES = {1: "Boot Sector", 2: "Memory Dump", 3: "Hard Maze",
                4: "Rogue Process", 5: "Konami Code", 6: "Fruit Ninja"}

SUBMISSION_WATCHDOG = 60  # hard timeout for the whole submission pipeline
_MAZE_PREVIEW_CODE = "print(open('/puzzle/maze.txt').read())"
VALID_PUZZLES = range(0, 7)        # 0 (test) .. 6
MAX_CODE_LEN = 65536               # reject oversized payloads

_solved_log_lock = threading.Lock()

# Guards against double-submitting the same (team, puzzle): a submission in flight
# (or already solved) is reserved so a concurrent duplicate is rejected up front.
_inflight: set[tuple[str, int]] = set()
_inflight_lock = threading.Lock()


def _reserve(team: str, puzzle: int) -> bool:
    # is_solved() reads teams.json — keep that file I/O out of the lock, which only
    # needs to guard the in-memory in-flight set. _mark_solved (before _release) is
    # the authoritative persistence, so a slightly stale read here is safe.
    if is_solved(team, puzzle):
        return False
    key = (team.strip().lower(), puzzle)
    with _inflight_lock:
        if key in _inflight:
            return False
        _inflight.add(key)
        return True


def _release(team: str, puzzle: int) -> None:
    with _inflight_lock:
        _inflight.discard((team.strip().lower(), puzzle))

# Shared secret for every POST endpoint: /submit and the display/control endpoints
# (daemon + mini-games are the legitimate callers). UNSET → auth disabled (logged), so
# existing deploys keep working; set OCRCADE_OPERATOR_TOKEN in the env on both server
# and daemon to enforce.
OPERATOR_TOKEN = os.environ.get("OCRCADE_OPERATOR_TOKEN", "")

log = logging.getLogger("ocrcade.server")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(levelname)-8s  %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])

app = Flask(__name__)
display = Display()

if not OPERATOR_TOKEN:
    log.warning("OCRCADE_OPERATOR_TOKEN not set — display/control endpoints are UNAUTHENTICATED")


def require_operator(view):
    """Gate internal display/control endpoints behind the shared operator token.
    No-op when the token is unset (keeps dev/un-provisioned deploys working)."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        provided = request.headers.get("X-Operator-Token", "")
        if OPERATOR_TOKEN and not hmac.compare_digest(provided, OPERATOR_TOKEN):
            return jsonify({"error": "forbidden"}), 403
        return view(*args, **kwargs)
    return wrapper


def _append_solved_log(team: str, puzzle: int) -> None:
    entry = {"team": team, "puzzle": puzzle,
             "name": PUZZLE_NAMES.get(puzzle, f"Puzzle {puzzle}"),
             "ts": time.strftime("%H:%M")}
    try:
        with _solved_log_lock:
            existing = json.loads(SOLVED_LOG.read_text()) if SOLVED_LOG.exists() else []
            existing.append(entry)
            SOLVED_LOG.write_text(json.dumps(existing, indent=2))
    except Exception as e:
        log.warning(f"Could not write solved_log.json: {e}")


def _init_level4_maze() -> None:
    maze, solution = generate_maze(15, 15)
    set_level4_maze(maze, solution)
    log.info(f"Level 4 maze generated, solution length={len(solution)}")


# ── HTTP endpoints ───────────────────────────────────────────────────────────

@app.route("/submit", methods=["POST"])
@require_operator  # runs player code; the daemon is the only caller and sends the token
def submit():
    data = request.get_json(force=True)
    puzzle = data.get("puzzle")
    team = data.get("team", "Unknown")
    lang = data.get("lang", "python")
    code = data.get("code", "")
    if puzzle is None or not code:
        return jsonify({"error": "Missing puzzle or code"}), 400
    try:
        puzzle = int(puzzle)
    except (TypeError, ValueError):
        return jsonify({"error": "puzzle must be an integer"}), 400
    if puzzle not in VALID_PUZZLES:
        return jsonify({"error": "puzzle out of range (0-6)"}), 400
    if not isinstance(code, str) or len(code) > MAX_CODE_LEN:
        return jsonify({"error": "code missing or too large"}), 400
    log.info(f"Received submission: puzzle={puzzle} team={team} lang={lang}")
    threading.Thread(target=_watchdog, args=(puzzle, team, lang, code),
                     daemon=True).start()
    return jsonify({"status": "accepted"}), 202


@app.route("/reset", methods=["POST"])
@require_operator
def reset():
    _init_level4_maze()
    display.attract()
    return jsonify({"status": "reset"}), 200


@app.route("/preview", methods=["POST"])
@require_operator
def preview():
    d = request.get_json(force=True)
    display.preview(d.get("ocr_lines", []), float(d.get("stability", 0.0)),
                    float(d.get("max_stability", 2.0)), d.get("source_type", "unknown"))
    return jsonify({"status": "ok"}), 200


@app.route("/feedback", methods=["POST"])
@require_operator
def feedback():
    d = request.get_json(force=True)
    display.parse_error(d.get("error", "OCR ERROR"), d.get("ocr_lines", []))
    return jsonify({"status": "ok"}), 200


@app.route("/konami_progress", methods=["POST"])
@require_operator
def konami_progress():
    d = request.get_json(force=True)
    display.konami_challenge(d.get("team", "?"), int(d.get("step", 0)),
                             int(d.get("attempts_left", 3)), float(d.get("elapsed", 0.0)),
                             float(d.get("timeout", 90.0)), d.get("wrong_press"),
                             int(d.get("ready_countdown", 0)))
    return jsonify({"status": "ok"}), 200


@app.route("/camera_feed", methods=["POST"])
@require_operator
def camera_feed():
    display.camera_feed(request.get_json(force=True).get("thumb", ""))
    return jsonify({"status": "ok"}), 200


@app.route("/fruitage_pause", methods=["POST"])
@require_operator
def fruitage_pause():
    display.fruitage_start()
    return jsonify({"status": "ok"}), 200


@app.route("/fruitage_resume", methods=["POST"])
@require_operator
def fruitage_resume():
    display.fruitage_end()
    return jsonify({"status": "ok"}), 200


@app.route("/maze_preview", methods=["POST"])
@require_operator
def maze_preview():
    maze_path = PUZZLES_DIR / "level3" / "maze_fixed.txt"
    if not maze_path.exists():
        return jsonify({"status": "error", "message": "maze file not found"}), 404
    display.maze_preview(maze_path.read_text())
    return jsonify({"status": "ok"}), 200


@app.route("/health", methods=["GET"])
def health():
    cg = sandbox_cgroup()
    return jsonify({"status": "ok", "sandbox_memory_cap": cg.memory_capped,
                    "sandbox_cgroup": cg.status}), 200


# ── Submission pipeline ──────────────────────────────────────────────────────

class Outcome:
    """One terminal screen per submission. The watchdog and the worker race for it."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._closed = False

    def claim(self, paint: Callable[[], None]) -> bool:
        """Paint the one terminal screen. A paint that raises does not count."""
        with self._lock:
            if self._closed:
                return False
            paint()
            self._closed = True
            return True

    def paint_if_open(self, paint: Callable[[], None]) -> bool:
        """Paint a progress screen. A closed outcome keeps its terminal screen."""
        with self._lock:
            if self._closed:
                return False
            paint()
            return True


def _watchdog(puzzle: int, team: str, lang: str, code: str) -> None:
    outcome = Outcome()
    inner = threading.Thread(target=_handle_submission,
                             args=(puzzle, team, lang, code, outcome), daemon=True)
    inner.start()
    inner.join(timeout=SUBMISSION_WATCHDOG)
    if inner.is_alive():
        log.error(f"Submission hung after {SUBMISSION_WATCHDOG}s — team={team!r} puzzle={puzzle}")
        outcome.claim(lambda: display.failure(team, "SYSTEM TIMEOUT – contact operator"))


def _handle_submission(puzzle: int, team: str, lang: str, code: str, outcome: Outcome) -> None:
    try:
        _run_submission(puzzle, team, lang, code, outcome)
    except Exception as e:
        log.exception(f"Unhandled error in submission thread: {e}")
        outcome.claim(lambda: display.failure(team, "INTERNAL ERROR – contact operator"))


def _show_maze_preview(puzzle: int, team: str) -> None:
    maze_path = PUZZLES_DIR / f"level{puzzle}" / "maze_fixed.txt"
    if not maze_path.exists():
        log.warning(f"Maze preview: no maze file for puzzle {puzzle}")
        display.failure(team, f"NO MAZE FOR PUZZLE {puzzle}")
        return
    display.maze_preview(maze_path.read_text())


def _run_submission(puzzle: int, team: str, lang: str, code: str, outcome: Outcome) -> None:
    if code.strip() == _MAZE_PREVIEW_CODE:
        outcome.claim(lambda: _show_maze_preview(puzzle, team))
        return

    # Atomically claim this (team, puzzle) so two near-simultaneous submissions
    # can't both pass the already-solved check and double-submit. Puzzle 0 (test)
    # has no scoring, so it isn't reserved.
    if puzzle != 0 and not _reserve(team, puzzle):
        log.info(f"Team '{team}' already solved / in-flight puzzle {puzzle} – rejecting")
        outcome.claim(lambda: display.failure(
            team, "ALREADY SOLVED – Your team already earned points for this puzzle"))
        return
    try:
        _finish_submission(puzzle, team, lang, code, outcome)
    finally:
        if puzzle != 0:
            _release(team, puzzle)


def _score(puzzle: int, team: str, message: str, outcome: Outcome) -> None:
    """Score and paint success together, so a timeout cannot land in between."""
    def finish() -> None:
        if puzzle == 0:
            log.info(f"Test puzzle passed for team '{team}' – no points awarded")
        else:
            ok, rtb_msg = rtb_submit(team, puzzle, message)
            log.info(f"RTB submit result: {ok} – {rtb_msg}")
            _append_solved_log(team, puzzle)
        display.success(team, puzzle, message)

    outcome.claim(finish)


def _finish_submission(puzzle: int, team: str, lang: str, code: str, outcome: Outcome) -> None:
    # Camera-challenge puzzles (5/6): token validated directly, no sandbox.
    if puzzle in (5, 6):
        success, message = validate(puzzle, code)
        if success:
            _score(puzzle, team, message, outcome)
        else:
            outcome.claim(lambda: display.failure(team, message))
        return

    if not outcome.paint_if_open(lambda: display.received(team, puzzle)):
        return
    result = sandbox_run(puzzle, lang, code)
    if result["busy"]:
        outcome.claim(lambda: display.failure(
            team, "BUSY – another team's code is running, show it again"))
        return
    if result["oom"]:
        outcome.claim(lambda: display.failure(
            team, "MEMORY LIMIT EXCEEDED – Your code used over 1 GB"))
        return
    if result["timed_out"]:
        outcome.claim(lambda: display.failure(
            team, "TIME LIMIT EXCEEDED – Your code took too long"))
        return

    if not outcome.paint_if_open(
            lambda: display.executing(result["stdout"], result["stderr"])):
        return
    success, message = validate(puzzle, result["stdout"])
    if success:
        _score(puzzle, team, message, outcome)
    else:
        outcome.claim(lambda: display.failure(team, message))


def main() -> None:
    cfg = load_config()["server"]
    _init_level4_maze()
    # Before any thread starts: the cgroup setup moves this process into server/.
    log.info(f"sandbox: {sandbox_cgroup().status}")
    display.attract()
    app.run(host=cfg.get("host", "0.0.0.0"), port=cfg.get("port", 5000), threaded=True)


if __name__ == "__main__":
    main()
