#!/usr/bin/env python3
"""
OCRcade retro display manager — direct framebuffer renderer.

Writes directly to /dev/fb0 (RGB565) using Pillow.
No X11, Wayland, or EGL required.

State machine: ATTRACT → RECEIVED → EXECUTING → SUCCESS/FAIL → ATTRACT
"""

import base64
import fcntl
import json
import logging
import math
import os
import textwrap
import threading
import time
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger('ocrcade.display')

# ── Display geometry ────────────────────────────────────────────────────────────
FB_DEV   = '/dev/fb0'
FB_BLANK = Path('/sys/class/graphics/fb0/blank')
FB_RETRY_S = 2.0
FBIOBLANK = 0x4611  # linux/fb.h — arg 0 is FB_BLANK_UNBLANK


def unblank(path: Path = FB_BLANK) -> None:
    """Wake a monitor that slept because the framebuffer was powered down."""
    try:
        path.write_text('0')
    except OSError as e:
        log.warning(f"could not unblank {path}: {e}")


def wake(fb, path: Path = FB_BLANK) -> None:
    """Unblank through the open framebuffer, then sysfs if that ioctl is refused.

    The exec service runs as the login user, who can write /dev/fb0. FBIOBLANK
    uses the same kernel call as the sysfs blank file. The write is the fallback
    when that ioctl is refused.
    """
    try:
        fcntl.ioctl(fb, FBIOBLANK, 0)
        return
    except OSError as e:
        log.warning(f"framebuffer unblank ioctl failed: {e}")
    unblank(path)


def open_fb(dev: str = FB_DEV):
    """Open the framebuffer, or None when it does not exist yet.

    No O_CREAT: open(..., 'wb') would create a regular file when the parent
    directory is writable, and writing a picture into that file does not wake
    the monitor. O_CLOEXEC matches open(), so a sandbox child does not inherit
    the display.
    """
    try:
        fd = os.open(dev, os.O_WRONLY | os.O_CLOEXEC)
    except OSError:
        return None
    return os.fdopen(fd, 'wb')


def _read_fb_geometry() -> tuple[int, int, int]:
    """Framebuffer geometry from sysfs, with an off-Pi fallback so the module
    stays importable on a dev box / in CI (the real fb is only used on kepler)."""
    try:
        size = Path('/sys/class/graphics/fb0/virtual_size').read_text().strip().split(',')
        bpp = int(Path('/sys/class/graphics/fb0/bits_per_pixel').read_text().strip())
        return int(size[0]), int(size[1]), bpp
    except (OSError, ValueError):
        return 1920, 1080, 16


WIDTH, HEIGHT, BPP = _read_fb_geometry()

# Scale all pixel values relative to the 1920×1080 design resolution
_SCALE = HEIGHT / 1080.0

def _s(v: int) -> int:
    """Scale a design-resolution pixel value to the actual screen size."""
    return max(1, int(v * _SCALE))

# ── Palette (RGB tuples) ────────────────────────────────────────────────────────
BLACK     = (0,   0,   0  )
GREEN     = (0,   255, 70 )
GREEN_DIM = (0,   140, 40 )
AMBER     = (255, 176, 0  )
AMBER_DIM = (140, 90,  0  )
RED       = (255, 60,  60 )
RED_DIM   = (100, 0,   0  )
CYAN      = (0,   220, 220)
WHITE     = (230, 230, 230)
DARK      = (8,   8,   8  )

SUCCESS_RESET     = 15   # seconds on SUCCESS before returning to attract
FAIL_RESET        = 5    # seconds on FAILURE/PARSE_ERROR before returning to attract
SCANNING_TIMEOUT  = 4.0  # seconds without a preview ping → return to attract

# Points awarded per puzzle (shown on success screen)
PUZZLE_POINTS = {0: 0, 1: 100, 2: 350, 3: 750, 4: 900, 5: 50, 6: 200}
SOLVED_LOG    = Path(__file__).resolve().parent.parent / 'solved_log.json'

ATTRACT_LINES = [
    ">>> OCRcade v2.0 <<<",
    "",
    "PAPER CODE ONLY",
    "NO LAPTOPS OR SCREENS",
    "OR ELSE! ;-)",
    "",
    "[ PUZZLE 0 ]  System Check      0 pts",
    "[ PUZZLE 1 ]  Boot Sector     100 pts",
    "[ PUZZLE 2 ]  Memory Dump     350 pts",
    "[ PUZZLE 3 ]  Hard Maze       750 pts",
    "[ PUZZLE 4 ]  Rogue Process   900 pts",
    "[ PUZZLE 5 ]  Konami Code      50 pts",
    "[ PUZZLE 6 ]  Fruit Ninja     200 pts",
    "",
    "Write code on paper.",
    "Hold it steady under the camera.",
    "",
    "TIP: Write ZERO as 0 (with slash), not O (letter)",
    "",
    ">>> SHOW A PUZZLE CARD TO START <<<",
]

# Konami sequence symbols shown on the challenge screen
_KONAMI_SYMBOLS = ['↑', '↑', '↓', '↓', '←', '→', '←', '→', '✕', '□']

FAIL_MSGS = [
    "SEGMENTATION FAULT",
    "KERNEL PANIC",
    "SYNTAX ERROR IN REALITY",
    "DIVIDE BY ZERO",
    "ILLEGAL INSTRUCTION",
    "BUS ERROR - TRY AGAIN",
    "REALITY.EXE HAS CRASHED",
]

# ── Font loading ────────────────────────────────────────────────────────────────
def _find_font(preferred: list[str], size: int) -> ImageFont.FreeTypeFont:
    for path in preferred:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()

_FONT_PATHS = [
    '/usr/share/fonts/truetype/hack/Hack-Regular.ttf',   # slashed zero
    '/usr/share/fonts/truetype/freefont/FreeMono.ttf',
    '/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf',
]
_FONT_PATHS_BOLD = [
    '/usr/share/fonts/truetype/hack/Hack-Bold.ttf',      # slashed zero
    '/usr/share/fonts/truetype/freefont/FreeMonoBold.ttf',
    '/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf',
]

FONT_XL = _find_font(_FONT_PATHS_BOLD, _s(120))
FONT_LG = _find_font(_FONT_PATHS_BOLD, _s(56))
FONT_MD = _find_font(_FONT_PATHS,      _s(36))
FONT_SM = _find_font(_FONT_PATHS,      _s(24))


# ── RGB565 conversion ───────────────────────────────────────────────────────────
def _to_rgb565(img: Image.Image) -> bytes:
    """Convert PIL RGB image to packed RGB565 bytes for /dev/fb0."""
    rgb = img.convert('RGB')
    r, g, b = rgb.split()
    import numpy as np
    r_arr = np.array(r, dtype=np.uint16)
    g_arr = np.array(g, dtype=np.uint16)
    b_arr = np.array(b, dtype=np.uint16)
    rgb565 = ((r_arr >> 3) << 11) | ((g_arr >> 2) << 5) | (b_arr >> 3)
    return rgb565.astype(np.uint16).tobytes()


def _to_framebuffer(img: Image.Image) -> bytes:
    if BPP == 16:
        return _to_rgb565(img)
    # 32-bit RGBA / BGRA
    return img.convert('RGBA').tobytes()


# ── Display class ───────────────────────────────────────────────────────────────
def _hide_cursor():
    """Suppress the blinking terminal cursor on the framebuffer console."""
    try:
        Path('/sys/class/graphics/fbcon/cursor_blink').write_text('0')
    except OSError:
        pass
    try:
        # Also blank the text console so no TTY text bleeds through
        with open('/dev/tty1', 'wb') as tty:
            tty.write(b'\033[?25l')   # hide cursor
            tty.write(b'\033[2J')     # clear screen
    except OSError:
        pass


class Display:
    def __init__(self):
        _hide_cursor()
        self._state         = 'attract'
        self._state_data    = {}
        self._state_entered = time.time()
        self._last_preview  = 0.0   # timestamp of last /preview ping
        self._thumb_b64     = ''    # latest camera thumbnail from Vision Pi
        self._fb_paused     = False # True while Fruit Ninja owns the framebuffer
        self._lock          = threading.Lock()
        self._fb            = open_fb()
        self._fb_retry_at   = time.monotonic()
        if self._fb is None:
            log.warning(f"No framebuffer at {FB_DEV} — display retries until it appears")
        else:
            wake(self._fb)
        self._thread        = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    # ── Public API ──────────────────────────────────────────────────────────────

    def attract(self):
        self._set('attract', {})

    def received(self, team: str, puzzle: int):
        self._set('received', {'team': team, 'puzzle': puzzle})

    def executing(self, stdout: str, stderr: str):
        self._set('executing', {'stdout': stdout, 'stderr': stderr})

    def success(self, team: str, puzzle: int):
        self._set('success', {'team': team, 'puzzle': puzzle})

    def failure(self, team: str, message: str, ocr: str = ''):
        self._set('failure', {'team': team, 'message': message, 'ocr': ocr})

    def preview(self, ocr_lines: list[str], stability: float, max_stability: float,
                source_type: str = 'unknown'):
        """Vision Pi is scanning — show live OCR preview with stability bar.

        While it waits for a puzzle card (source 'await_card') the main page shows.
        """
        with self._lock:
            if source_type == 'await_card':
                if self._state == 'scanning':
                    self._state = 'attract'
                    self._state_data = {}
                    self._state_entered = time.time()
                return
            if self._state in ('attract', 'scanning'):
                if self._state != 'scanning':
                    self._state = 'scanning'
                    self._state_entered = time.time()
                self._state_data = {
                    'ocr_lines':    ocr_lines,
                    'stability':    stability,
                    'max_stability': max_stability,
                    'source_type':  source_type,
                }
                self._last_preview = time.time()

    def maze_preview(self, maze_text: str):
        """Show maze layout for SUCCESS_RESET seconds so player can study it."""
        self._set('maze_preview', {'maze_text': maze_text})

    def camera_feed(self, thumb_b64: str):
        """Vision Pi posts a live camera thumbnail for the alignment overlay."""
        with self._lock:
            self._thumb_b64 = thumb_b64

    def parse_error(self, message: str, ocr_lines: list[str]):
        """Parse/read error — clean diagnostic display, no retro fail messages."""
        self._set('parse_error', {'message': message, 'ocr_lines': ocr_lines})

    def fruitage_start(self):
        """Show the Fruit Ninja screen, then pause fb0 so pygame can take over."""
        self._set('fruitage_active', {})
        time.sleep(0.15)   # let the render loop write the screen at least once
        with self._lock:
            self._fb_paused = True

    def fruitage_end(self):
        """Resume fb0 writes after Fruit Ninja exits."""
        with self._lock:
            fb = self._fb
        if fb is not None:
            wake(fb)
        else:
            unblank()
        with self._lock:
            self._fb_paused = False
        self._set('attract', {})

    def konami_challenge(
        self,
        team: str,
        step: int,
        attempts_left: int,
        elapsed: float,
        timeout: float,
        wrong_press: str | None = None,
        ready_countdown: int = 0,
    ):
        """Vision Pi posts live Konami sequence progress."""
        with self._lock:
            if self._state not in ('konami_challenge',):
                self._state         = 'konami_challenge'
                self._state_entered = time.time()
            self._state_data = {
                'team':            team,
                'step':            step,
                'attempts_left':   attempts_left,
                'elapsed':         elapsed,
                'timeout':         timeout,
                'wrong_press':     wrong_press,
                'ready_countdown': ready_countdown,
            }

    # ── Internal ────────────────────────────────────────────────────────────────

    def _set(self, state: str, data: dict):
        with self._lock:
            self._state        = state
            self._state_data   = data
            self._state_entered = time.time()

    def _loop(self):
        fps_interval = 1 / 15   # 15 fps — plenty for this use case
        while True:
            t0 = time.time()
            with self._lock:
                state        = self._state
                data         = dict(self._state_data)
                elapsed      = time.time() - self._state_entered
                last_preview = self._last_preview

            img  = Image.new('RGB', (WIDTH, HEIGHT), DARK)
            draw = ImageDraw.Draw(img)

            if state == 'attract':
                self._draw_attract(draw, elapsed)
                self._draw_camera_thumb(img, draw)
            elif state == 'scanning':
                self._draw_scanning(draw, data, elapsed)
                self._draw_camera_thumb(img, draw)
                if time.time() - last_preview > SCANNING_TIMEOUT:
                    self._set('attract', {})
            elif state == 'received':
                self._draw_received(draw, data)
            elif state == 'executing':
                self._draw_executing(draw, data, elapsed)
            elif state == 'konami_challenge':
                self._draw_konami_challenge(draw, data, elapsed)
            elif state == 'maze_preview':
                self._draw_maze_preview(draw, data, elapsed)
                if elapsed > SUCCESS_RESET:
                    self._set('attract', {})
            elif state == 'success':
                self._draw_success(draw, data, elapsed)
                if elapsed > SUCCESS_RESET:
                    self._set('attract', {})
            elif state == 'failure':
                self._draw_failure(draw, data, elapsed)
                if elapsed > FAIL_RESET:
                    self._set('attract', {})
            elif state == 'parse_error':
                self._draw_parse_error(draw, data, elapsed)
                if elapsed > FAIL_RESET:
                    self._set('attract', {})
            elif state == 'fruitage_active':
                self._draw_fruitage_active(draw, elapsed)

            self._draw_scanlines(img)
            self._blit(img)

            elapsed_render = time.time() - t0
            sleep_time = fps_interval - elapsed_render
            if sleep_time > 0:
                time.sleep(sleep_time)

    def _reopen_fb(self) -> None:
        now = time.monotonic()
        if now - self._fb_retry_at < FB_RETRY_S:
            return
        self._fb_retry_at = now
        fb = open_fb()
        if fb is None:
            return
        self._fb = fb
        wake(fb)
        log.info(f"framebuffer open at {FB_DEV}")

    def _blit(self, img: Image.Image):
        with self._lock:
            if self._fb_paused:
                return
        if self._fb is None:
            self._reopen_fb()
        if self._fb is None:
            return
        data = _to_framebuffer(img)
        self._fb.seek(0)
        self._fb.write(data)
        self._fb.flush()

    # ── Screens ─────────────────────────────────────────────────────────────────

    def _load_solved_log(self) -> list[dict]:
        try:
            if SOLVED_LOG.exists():
                return json.loads(SOLVED_LOG.read_text())
        except Exception:
            pass
        return []

    def _draw_attract(self, draw: ImageDraw.ImageDraw, elapsed: float):
        blink = int(elapsed * 2) % 2 == 0
        self._border(draw, AMBER_DIM)

        # Center the list above the bottom strip, which holds the hall-of-fame line.
        line_h = _s(44)
        y = (HEIGHT - _s(120)) // 2 - (len(ATTRACT_LINES) * line_h) // 2
        for line in ATTRACT_LINES:
            color = AMBER if line.startswith('>>>') else GREEN
            if line.startswith('>>>') and not blink:
                color = AMBER_DIM
            self._centered_text(draw, line, y, color, FONT_MD)
            y += line_h

        # Hall of fame — scroll through captures at the bottom
        solved = self._load_solved_log()
        if solved:
            idx  = int(elapsed / 3) % len(solved)   # rotate every 3 seconds
            entry = solved[idx]
            hof_text = (f"\u2605  {entry['team'].upper()}  solved  "
                        f"{entry['name']}  at {entry['ts']}  \u2605")
            color = AMBER if blink else AMBER_DIM
            self._centered_text(draw, hof_text, HEIGHT - _s(80), color, FONT_SM)

    def _draw_scanning(self, draw: ImageDraw.ImageDraw, data: dict, elapsed: float):
        """Live OCR preview with stability progress bar."""
        source = data.get('source_type', '')
        if source == 'puzzle_seen':
            self._draw_task(draw, data, elapsed)
            return
        if source == 'ready_code':
            self._draw_cue(draw, data, AMBER)
            return
        self._border(draw, CYAN)

        spinner = ['|', '/', '-', '\\'][int(elapsed * 6) % 4]
        self._centered_text(draw, f'{spinner}  SCANNING  {spinner}', _s(40), CYAN, FONT_MD)

        source_type = data.get('source_type', 'unknown')
        source_label = {'paper': 'Paper', 'screen': 'Screen/Tablet', 'unknown': 'Unknown'}.get(source_type, source_type.capitalize())
        source_color = {'paper': GREEN, 'screen': CYAN, 'unknown': AMBER_DIM}.get(source_type, WHITE)
        draw.text((60, _s(110)), f'Camera sees:   [ Detected: {source_label} ]', font=FONT_SM, fill=source_color)
        draw.line([(60, _s(140)), (WIDTH - 60, _s(140))], fill=CYAN, width=1)

        ocr_lines = data.get('ocr_lines', [])
        y = _s(155)
        for line in ocr_lines[:12]:
            if line.startswith(('Puzzle', 'Team', 'Lang')):
                color = AMBER
            elif '←' in line:
                color = GREEN_DIM
            else:
                color = GREEN
            draw.text((80, y), line[:72], font=FONT_SM, fill=color)
            y += _s(30)
            if y > HEIGHT - _s(150):
                break

        # Stability progress bar
        stability     = data.get('stability', 0.0)
        max_stability = data.get('max_stability', 2.0)
        bar_x = 60
        bar_y = HEIGHT - _s(110)
        bar_w = WIDTH - 120
        bar_h = _s(28)
        filled = int(bar_w * min(stability / max(max_stability, 0.01), 1.0))

        draw.rectangle([bar_x, bar_y, bar_x + bar_w, bar_y + bar_h],
                       outline=CYAN, width=2)
        if filled > 4:
            draw.rectangle([bar_x + 2, bar_y + 2, bar_x + filled - 2, bar_y + bar_h - 2],
                           fill=CYAN)

        label = f'{stability:.1f}s / {max_stability:.1f}s — hold steady'
        self._centered_text(draw, label, HEIGHT - _s(68), AMBER_DIM, FONT_SM)

    def _draw_task(self, draw: ImageDraw.ImageDraw, data: dict, elapsed: float):
        """The scanned puzzle and its task. The last line says what to do next."""
        self._border(draw, GREEN)
        lines = [str(ln) for ln in data.get('ocr_lines', [])]
        if len(lines) < 3:
            self._draw_cue(draw, data, GREEN)
            return
        title, subtitle, *task, hint = lines
        y = _s(140)
        self._centered_text(draw, title, y, AMBER, FONT_XL)
        y += _s(170)
        self._centered_text(draw, subtitle, y, WHITE, FONT_LG)
        y += _s(130)
        for line in task:
            self._centered_text(draw, line, y, GREEN, FONT_MD)
            y += _s(56)
        blink = int(elapsed * 2) % 2 == 0
        self._centered_text(draw, hint, HEIGHT - _s(140), AMBER if blink else AMBER_DIM, FONT_MD)

    def _draw_cue(self, draw: ImageDraw.ImageDraw, data: dict, color: tuple):
        """Full-screen beat: the puzzle confirmation, then "ready your code"."""
        self._border(draw, color)
        lines = [str(ln) for ln in data.get('ocr_lines', []) if str(ln).strip()][:3]
        fonts = (FONT_XL, FONT_LG, FONT_MD)
        fills = (AMBER, WHITE, GREEN)
        gap = _s(36)
        heights = []
        for i, line in enumerate(lines):
            bbox = draw.textbbox((0, 0), line, font=fonts[i])
            heights.append(bbox[3] - bbox[1])
        total = sum(heights) + gap * max(len(lines) - 1, 0)
        y = (HEIGHT - total) // 2
        for i, line in enumerate(lines):
            self._centered_text(draw, line, y, fills[i], fonts[i])
            y += heights[i] + gap

    def _draw_received(self, draw: ImageDraw.ImageDraw, data: dict):
        self._border(draw, CYAN)
        self._centered_text(draw, 'CODE RECEIVED',   HEIGHT // 2 - _s(80), CYAN,  FONT_LG)
        self._centered_text(draw,
            f"Puzzle {data.get('puzzle')}  |  Team: {data.get('team')}",
            HEIGHT // 2, GREEN, FONT_MD)
        self._centered_text(draw, 'EXECUTING...',    HEIGHT // 2 + _s(80), AMBER, FONT_MD)

    def _draw_executing(self, draw: ImageDraw.ImageDraw, data: dict, elapsed: float):
        self._border(draw, GREEN_DIM)
        self._centered_text(draw, '>> EXECUTING <<', _s(40), GREEN, FONT_MD)
        spinner = ['|', '/', '-', '\\'][int(elapsed * 8) % 4]
        self._centered_text(draw, spinner, _s(100), AMBER, FONT_MD)

        output = (data.get('stdout', '') + data.get('stderr', '')).strip()
        lines  = output.splitlines()[-18:]
        y = _s(160)
        for line in lines:
            for chunk in (textwrap.wrap(line, width=90) or ['']):
                draw.text((40, y), chunk, font=FONT_SM, fill=GREEN)
                y += _s(28)

    def _draw_success(self, draw: ImageDraw.ImageDraw, data: dict, elapsed: float):
        pulse = abs(math.sin(elapsed * 3))
        color = (
            int(GREEN[0] * pulse + AMBER[0] * (1 - pulse)),
            int(GREEN[1] * pulse + AMBER[1] * (1 - pulse)),
            0,
        )
        self._border(draw, color)

        puzzle = data.get('puzzle', 0)
        team   = data.get('team', '?').upper()
        points = PUZZLE_POINTS.get(puzzle, 0)

        self._centered_text(draw, '*** CONGRATULATIONS ***',  HEIGHT // 2 - _s(160), color,  FONT_LG)
        self._centered_text(draw, team,                        HEIGHT // 2 - _s(60),  WHITE,  FONT_LG)
        self._centered_text(draw,
            f"PUZZLE {puzzle} COMPLETE",
            HEIGHT // 2 + _s(60), GREEN, FONT_MD)
        if points > 0:
            self._centered_text(draw,
                f"+{points} POINTS AWARDED",
                HEIGHT // 2 + _s(120), AMBER, FONT_MD)
        self._centered_text(draw,
            f"RESETTING IN {max(0, int(SUCCESS_RESET - elapsed))}s",
            HEIGHT - _s(80), AMBER_DIM, FONT_SM)

    def _draw_maze_preview(self, draw: ImageDraw.ImageDraw, data: dict, elapsed: float):
        """Show the maze so the player can study it before writing their solution."""
        self._border(draw, CYAN)
        self._centered_text(draw, 'MAZE LAYOUT', _s(30), CYAN, FONT_MD)
        draw.line([(60, _s(85)), (WIDTH - 60, _s(85))], fill=CYAN, width=1)

        maze_text = data.get('maze_text', '').strip()
        lines = maze_text.splitlines()
        y = _s(100)
        for line in lines:
            draw.text((60, y), line, font=FONT_SM, fill=GREEN)
            y += _s(26)
            if y > HEIGHT - _s(80):
                break

        self._centered_text(draw,
            f"RESETTING IN {max(0, int(SUCCESS_RESET - elapsed))}s",
            HEIGHT - _s(60), AMBER_DIM, FONT_SM)

    def _draw_konami_challenge(self, draw: ImageDraw.ImageDraw, data: dict, elapsed: float):
        """Live Konami sequence challenge screen."""
        team            = data.get('team', '?').upper()
        step            = int(data.get('step', 0))
        attempts_left   = int(data.get('attempts_left', 3))
        vpi_elapsed     = float(data.get('elapsed', 0.0))
        timeout         = float(data.get('timeout', 90.0))
        wrong_press     = data.get('wrong_press')
        ready_countdown = int(data.get('ready_countdown', 0))

        remaining = max(0.0, timeout - vpi_elapsed)
        blink     = int(elapsed * 4) % 2 == 0

        self._border(draw, CYAN)

        # Header
        self._centered_text(draw, 'KONAMI CHALLENGE', _s(30), CYAN, FONT_MD)
        self._centered_text(draw, team, _s(85), WHITE, FONT_MD)

        # GET READY phase — big countdown, dim sequence in background
        if ready_countdown > 0:
            self._centered_text(draw, 'GET IN POSITION', HEIGHT // 2 - _s(100),
                                AMBER, FONT_MD)
            self._centered_text(draw,
                str(ready_countdown),
                HEIGHT // 2 - _s(20), AMBER if blink else AMBER_DIM, FONT_LG)
            self._centered_text(draw,
                'Hands near controller — do not touch yet',
                HEIGHT // 2 + _s(80), GREEN_DIM, FONT_SM)
            return

        draw.line([(60, _s(135)), (WIDTH - 60, _s(135))], fill=CYAN, width=1)

        # Sequence symbols row
        total      = len(_KONAMI_SYMBOLS)
        sym_w      = (WIDTH - 120) // total
        sym_y      = _s(175)
        for i, sym in enumerate(_KONAMI_SYMBOLS):
            sx = 60 + i * sym_w + sym_w // 2

            if i < step:
                color = GREEN          # completed
            elif i == step:
                color = AMBER if blink else AMBER_DIM   # next expected
            else:
                color = (40, 40, 40)   # not yet reached

            bbox = draw.textbbox((0, 0), sym, font=FONT_LG)
            tw = bbox[2] - bbox[0]
            draw.text((sx - tw // 2, sym_y), sym, font=FONT_LG, fill=color)

        draw.line([(60, _s(285)), (WIDTH - 60, _s(285))], fill=CYAN, width=1)

        # Wrong press flash
        _BUTTON_SYMS = {
            'UP': '↑', 'DOWN': '↓', 'LEFT': '←', 'RIGHT': '→',
            'CIRCLE': '○', 'CROSS': '✕', 'SQUARE': '□', 'TRIANGLE': '△',
        }
        if wrong_press:
            sym = _BUTTON_SYMS.get(wrong_press, wrong_press)
            self._centered_text(draw,
                f'WRONG! Pressed {sym} – sequence reset',
                _s(320), RED if blink else RED_DIM, FONT_MD)
        else:
            # Step counter
            self._centered_text(draw,
                f'Step {step} of {total}',
                _s(320), AMBER_DIM, FONT_MD)

        # Attempts
        attempts_str = '●' * attempts_left + '○' * (3 - attempts_left)
        self._centered_text(draw,
            f'Attempts remaining:  {attempts_str}',
            _s(390), GREEN if attempts_left > 1 else RED, FONT_MD)

        # Expected next
        if step < total:
            next_sym = _KONAMI_SYMBOLS[step]
            self._centered_text(draw,
                f'Press  {next_sym}',
                _s(470), AMBER if blink else AMBER_DIM, FONT_LG)

        # Countdown timer bar
        bar_x = 60
        bar_y = HEIGHT - _s(110)
        bar_w = WIDTH - 120
        bar_h = _s(28)
        fraction = remaining / max(timeout, 0.01)
        filled   = int(bar_w * fraction)
        bar_col  = GREEN if fraction > 0.4 else (AMBER if fraction > 0.2 else RED)

        draw.rectangle([bar_x, bar_y, bar_x + bar_w, bar_y + bar_h],
                       outline=CYAN, width=2)
        if filled > 4:
            draw.rectangle([bar_x + 2, bar_y + 2,
                            bar_x + filled - 2, bar_y + bar_h - 2],
                           fill=bar_col)
        self._centered_text(draw,
            f'{remaining:.0f}s remaining',
            HEIGHT - _s(68), AMBER_DIM, FONT_SM)

    def _draw_parse_error(self, draw: ImageDraw.ImageDraw, data: dict, elapsed: float):
        """Clean diagnostic screen for OCR / header parse failures."""
        blink = int(elapsed * 2) % 2 == 0
        self._border(draw, AMBER)

        # Error reason — big and visible
        self._centered_text(draw, '[ READ ERROR ]', _s(60), RED if blink else RED_DIM, FONT_LG)
        msg = data.get('message', 'Could not read paper')
        self._centered_text(draw, msg[:72], _s(150), AMBER, FONT_MD)

        # Divider
        draw.line([(60, _s(210)), (WIDTH - 60, _s(210))], fill=AMBER_DIM, width=2)

        # "Camera read:" heading
        draw.text((60, _s(230)), 'Camera read:', font=FONT_SM, fill=GREEN_DIM)

        # Formatted OCR lines (normalized: Puzzle/Team/Lang labels cleaned up)
        ocr_lines = data.get('ocr_lines', [])
        y = _s(265)
        for line in ocr_lines[:8]:
            color = AMBER if line.startswith(('Puzzle', 'Team', 'Lang')) else GREEN
            color = GREEN_DIM if '←' in line else color
            draw.text((80, y), line[:75], font=FONT_SM, fill=color)
            y += _s(32)
            if y > HEIGHT - _s(120):
                break

        # Divider + what to do. No countdown: a failed code read keeps the puzzle
        # and reads again, so a timer would restart on every retry.
        draw.line([(60, HEIGHT - _s(110)), (WIDTH - 60, HEIGHT - _s(110))], fill=AMBER_DIM, width=2)
        self._centered_text(draw, "Hold your code flat and steady, then try again",
                            HEIGHT - _s(85), AMBER_DIM, FONT_SM)

    def _draw_failure(self, draw: ImageDraw.ImageDraw, data: dict, elapsed: float):
        fail_msg = FAIL_MSGS[int(elapsed) % len(FAIL_MSGS)]
        blink    = int(elapsed * 3) % 2 == 0
        color    = RED if blink else RED_DIM

        self._border(draw, RED)
        self._centered_text(draw, fail_msg, HEIGHT // 2 - _s(140), color, FONT_LG)
        self._centered_text(draw, data.get('message', 'TRY AGAIN')[:70],
                            HEIGHT // 2 - _s(40), AMBER, FONT_MD)

        # Show what the camera actually read so players can self-diagnose
        ocr = data.get('ocr', '').strip()
        if ocr:
            ocr_lines = [ln.strip() for ln in ocr.splitlines() if ln.strip()][:3]
            draw.text((40, HEIGHT // 2 + _s(40)), "Camera read:", font=FONT_SM, fill=GREEN_DIM)
            for i, ln in enumerate(ocr_lines):
                draw.text((40, HEIGHT // 2 + _s(70) + i * _s(30)), ln[:80], font=FONT_SM, fill=GREEN)

        self._centered_text(draw,
            f"RESETTING IN {max(0, int(FAIL_RESET - elapsed))}s",
            HEIGHT - _s(80), AMBER_DIM, FONT_SM)

    def _draw_fruitage_active(self, draw: ImageDraw.ImageDraw, elapsed: float):
        """Shown while Fruit Ninja owns the framebuffer (fb writes are paused)."""
        blink = int(elapsed * 2) % 2 == 0
        self._border(draw, GREEN)
        self._centered_text(draw, 'FRUIT NINJA', HEIGHT // 2 - _s(100), GREEN, FONT_LG)
        self._centered_text(draw, 'CHALLENGE IN PROGRESS',
                            HEIGHT // 2, AMBER if blink else AMBER_DIM, FONT_MD)
        self._centered_text(draw, 'Step up and wave your hands!',
                            HEIGHT // 2 + _s(80), WHITE, FONT_SM)

    # ── Helpers ──────────────────────────────────────────────────────────────────

    def _draw_camera_thumb(self, img: Image.Image, draw: ImageDraw.ImageDraw):
        """Overlay the live camera thumbnail in the top-right corner."""
        with self._lock:
            thumb_b64 = self._thumb_b64
        if not thumb_b64:
            return
        try:
            thumb_img = Image.open(BytesIO(base64.b64decode(thumb_b64))).convert('RGB')
            tw = _s(320)
            th = _s(240)
            thumb_img = thumb_img.resize((tw, th), Image.Resampling.LANCZOS)
            pad = _s(12)
            tx = WIDTH - tw - pad
            ty = pad
            img.paste(thumb_img, (tx, ty))
            draw.rectangle([tx - 2, ty - 2, tx + tw + 2, ty + th + 2],
                           outline=CYAN, width=2)
            draw.text((tx, ty + th + _s(4)), 'CAMERA', font=FONT_SM, fill=CYAN)
        except Exception:
            pass

    def _centered_text(self, draw: ImageDraw.ImageDraw, text: str, y: int,
                       color: tuple, font: ImageFont.FreeTypeFont):
        bbox = draw.textbbox((0, 0), text, font=font)
        w = bbox[2] - bbox[0]
        x = (WIDTH - w) // 2
        draw.text((x, y), text, font=font, fill=color)

    def _border(self, draw: ImageDraw.ImageDraw, color: tuple, width: int = 4):
        for i in range(width):
            draw.rectangle([i, i, WIDTH - 1 - i, HEIGHT - 1 - i], outline=color)

    def _draw_scanlines(self, img: Image.Image):
        opacity = max(20, int(55 * _SCALE))   # lighter at smaller resolutions
        pitch   = max(4,  int(3  / _SCALE))   # wider gap at smaller resolutions
        line = Image.new('RGBA', (WIDTH, 1), (0, 0, 0, opacity))
        for y in range(0, HEIGHT, pitch):
            img.paste(line, (0, y), line)


if __name__ == '__main__':
    d = Display()
    d.attract()
    time.sleep(4)
    d.received('SakebombHax', 1)
    time.sleep(2)
    d.executing('Hello world\nRunning challenge...', '')
    time.sleep(3)
    d.success('SakebombHax', 1)
    time.sleep(6)
    d.failure('SakebombHax', 'Flag not found in output')
    time.sleep(6)
