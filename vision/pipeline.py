"""OCRcade capture pipeline — the ArUco-model state machine.

    IDLE     ── stable puzzle marker ──▶ SELECTED   (puzzle 5/6 wait, then challenge)
    SELECTED ── gate fires on code ────▶ READBACK   (VLM read code + roster team)
    READBACK ── confirm GO ───────────▶ submit ▶ IDLE
             ── cancel / timeout ─────▶ IDLE
    (a different marker in any state re-selects)

The logic is collaborator-injected (markers / gate / reader / ui / confirm /
submit / challenge) and clock-injected, so it unit-tests with fakes — no camera,
GPU, or real time. `daemon.py` wires the concrete IMX500 + VLM + display in.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from enum import StrEnum
from typing import Protocol

from game.roster import resolve_team

from .schema import CardReading

log = logging.getLogger("ocrcade.pipeline")

# The "READY YOUR CODE" screen's bottom line: what the player should do next.
SKIPS_BEFORE_HINT = 3      # reads skipped for "no text" in a row (~4 s) before HINT_CLOSER
HINT_SHOW = "Hold your code up to the camera"
HINT_STILL = "Hold still..."
HINT_BLURRY = "Too blurry. Hold the paper still."
HINT_CLOSER = "Can't see your code. Hold it closer so it fills the camera."
HINT_READING = "Reading your code..."


class State(StrEnum):
    IDLE = "idle"
    SELECTED = "selected"
    READBACK = "readback"


class UI(Protocol):
    """Display surface (daemon posts these to the exec/display server)."""
    def thumb(self, frame) -> None: ...
    def await_card(self) -> None: ...
    def puzzle_locked(self, puzzle: int, lang: str) -> None: ...
    def await_code(self, puzzle: int, lang: str, progress: float, hint: str = "") -> None: ...
    def readback(self, puzzle: int, lang: str, team: str | None, code: str) -> None: ...
    def feedback(self, title: str, detail: str) -> None: ...


class ConfirmSource(Protocol):
    """Read-back confirmation. Pollable so the loop never blocks.
    `arm()` starts a fresh wait; `poll()` returns 'go' | 'cancel' | None."""
    def arm(self) -> None: ...
    def poll(self) -> str | None: ...


class AutoConfirm:
    """Phase-B default: auto-submit after showing the read-back for `hold_s`.
    Replaced by a GPIO button (ButtonConfirm) in Phase C."""
    def __init__(self, hold_s: float = 4.0, now: Callable[[], float] = time.monotonic):
        self.hold_s = hold_s
        self._now = now
        self._armed_at = 0.0

    def arm(self) -> None:
        self._armed_at = self._now()

    def poll(self) -> str | None:
        return "go" if self._now() - self._armed_at >= self.hold_s else None


class Pipeline:
    def __init__(self, *, markers, gate, reader, roster: list[str], ui: UI,
                 submit: Callable[[dict], None], confirm: ConfirmSource,
                 challenge: Callable[[int, str], None],
                 marker_stable_frames: int = 2, roster_cutoff: float = 0.6,
                 select_timeout_s: float = 60.0, readback_timeout_s: float = 30.0,
                 cooldown_s: float = 20.0, settle_s: float = 0.0, clear_frames: int = 1,
                 confirm_s: float = 0.0, now: Callable[[], float] = time.monotonic,
                 captures=None):
        self.markers = markers
        self.gate = gate
        self.reader = reader
        self.roster = roster
        self.ui = ui
        self.submit = submit
        self.confirm = confirm
        self.challenge = challenge
        self.marker_stable_frames = marker_stable_frames
        self.roster_cutoff = roster_cutoff
        self.select_timeout_s = select_timeout_s
        self.readback_timeout_s = readback_timeout_s
        self.cooldown_s = cooldown_s
        self.settle_s = settle_s
        self.clear_frames = clear_frames
        self.confirm_s = confirm_s
        self._now = now
        self.captures = captures      # CaptureLog or None: saves each read's frame

        self.state = State.IDLE
        self._puzzle: int | None = None
        self._lang: str | None = None
        self._sel_marker_id: int | None = None
        self._marker_run_id: int | None = None
        self._marker_run = 0
        self._clear_run = 0
        self._stray_id: int | None = None
        self._stray_run = 0
        self._skips = 0            # reads skipped in a row because no text was in view
        self._t_state = 0.0
        self._cooldown_until = 0.0
        self._pending: CardReading | None = None
        self._pending_image: str | None = None   # where the pending read's frame was saved

    # --- one scan tick: lores for detect/gate, grab_main() for the hi-res read ---
    def on_frame(self, lores, grab_main: Callable[[], object]) -> State:
        self.ui.thumb(lores)
        if self._now() < self._cooldown_until:
            return self.state
        if self.state is State.IDLE:
            self._tick_idle(lores)
        elif self.state is State.SELECTED:
            self._tick_selected(lores, grab_main)
        elif self.state is State.READBACK:
            self._tick_readback()
        return self.state

    def _tick_idle(self, lores) -> None:
        m = self._stable_marker(lores)
        if m is None:
            self.ui.await_card()
            return
        self._select(m)

    def _tick_selected(self, lores, grab_main) -> None:
        # A different card replaces this one only after it holds still. One bad
        # frame used to launch Fruit Ninja while the player was showing puzzle 1.
        m = self.markers.pick(lores)
        if m is not None and m.marker_id != self._sel_marker_id:
            if self._stray_held(m):
                self._select_reset()
                self._select(m)
            else:
                self.gate.reset()
                # A camera puzzle never asks for code. One bad frame must not
                # flash "READY YOUR CODE" after the confirmation beat.
                if self._puzzle in (5, 6):
                    self.ui.puzzle_locked(self._puzzle, self._lang)
                else:
                    self._announce()
            return
        self._stray_id = None
        self._stray_run = 0
        if self._now() - self._t_state > self.select_timeout_s:
            log.info("SELECTED timeout → IDLE")
            self._to_idle()
            return
        if self._puzzle in (5, 6):
            self._tick_challenge(m)
            return
        if not self._ready_for_code(m):
            self.gate.reset()
            self._announce()
            return
        fired = self.gate.update(lores)
        progress = self.gate.last.get("progress", 0.0) if self.gate.last.get("has_text") else 0.0
        self._announce(progress)
        if fired:
            self._read(grab_main())

    def _tick_readback(self) -> None:
        decision = self.confirm.poll()
        if decision == "go":
            self._do_submit()
        elif decision == "cancel" or self._now() - self._t_state > self.readback_timeout_s:
            log.info("read-back cancelled/timed out → IDLE")
            self._to_idle()

    # --- transitions ---
    def _select(self, m) -> None:
        self._puzzle, self._lang, self._sel_marker_id = m.puzzle, m.lang, m.marker_id
        self.state = State.SELECTED
        self._t_state = self._now()
        self._clear_run = 0
        self._skips = 0
        self.gate.reset()
        log.info(f"SELECTED puzzle={m.puzzle} lang={m.lang} (marker {m.marker_id})")
        self.ui.puzzle_locked(m.puzzle, m.lang)

    def _tick_challenge(self, marker) -> None:
        """Puzzle 5/6 start only after the confirmation, and only if that card is still up."""
        held = marker is not None and marker.marker_id == self._sel_marker_id
        if not held:
            self._clear_run += 1
            self.ui.puzzle_locked(self._puzzle, self._lang)
            if self._clear_run >= self.clear_frames:
                log.info("challenge card left before start → IDLE")
                self._to_idle()
            return
        self._clear_run = 0
        if not self._confirmed():
            self.ui.puzzle_locked(self._puzzle, self._lang)
            return
        log.info(f"camera-challenge marker {self._sel_marker_id} → puzzle {self._puzzle}")
        self.challenge(self._puzzle, self._lang)
        self._cooldown()
        self._to_idle()

    def _read(self, main) -> None:
        # An empty scene (the room, a bare table) is sharp and still too, so the gate
        # fires on it. That is not a failed read: keep asking for code, no error.
        if not self.reader.sees_text(main):
            self._skips += 1
            self.gate.reset()
            return
        self._skips = 0
        self.ui.await_code(self._puzzle, self._lang, 1.0, HINT_READING)
        r = self.reader.read_code(main)
        team = resolve_team(r.code, self.roster, self.roster_cutoff) if r.readable else None
        image = self._capture(main, r, team)
        if not r.readable:
            log.info(f"code not read: {r.reason}")
            self.ui.feedback("CODE NOT READ", "Hold the paper so it fills the camera")
            self.gate.reset()                     # stay SELECTED — let them retry
            return
        if team is None:
            self.ui.feedback("TEAM NOT RECOGNISED", "add '# team: NAME' (registered)")
            self.gate.reset()
            return
        self._pending = CardReading(readable=True, puzzle=self._puzzle, lang=self._lang,
                                    team=team, code=r.code, backend=r.backend,
                                    latency_s=r.latency_s)
        self._pending_image = image
        self.state = State.READBACK
        self._t_state = self._now()
        self.confirm.arm()
        self.ui.readback(self._puzzle, self._lang, team, r.code or "")
        log.info(f"READBACK puzzle={self._puzzle} team={team!r} ({len(r.code or '')} chars)")

    def _do_submit(self) -> None:
        p = self._pending
        if p is not None:
            log.info(f"submit P{p.puzzle} team={p.team!r} ({len(p.code or '')} chars)")
            self.submit({"puzzle": p.puzzle, "team": p.team, "lang": p.lang, "code": p.code})
            if self.captures is not None:
                self.captures.mark_submitted(self._pending_image)
        self._cooldown()
        self._to_idle()

    # --- helpers ---
    def _capture(self, main, r: CardReading, team: str | None) -> str | None:
        if self.captures is None:
            return None
        return self.captures.save(main, r, self._puzzle, self._lang, team)

    def _stable_marker(self, lores):
        m = self.markers.pick(lores)
        mid = m.marker_id if m else None
        if mid is not None and mid == self._marker_run_id:
            self._marker_run += 1
        else:
            self._marker_run_id = mid
            self._marker_run = 1 if mid is not None else 0
        return m if (m is not None and self._marker_run >= self.marker_stable_frames) else None

    def _stray_held(self, marker) -> bool:
        if marker.marker_id == self._stray_id:
            self._stray_run += 1
        else:
            self._stray_id = marker.marker_id
            self._stray_run = 1
        return self._stray_run >= self.marker_stable_frames

    def _confirmed(self) -> bool:
        """The "got it" screen has been up long enough to read."""
        return self._now() - self._t_state >= self.confirm_s

    def _announce(self, progress: float = 0.0) -> None:
        """Show the puzzle's task while its card is up, for at least confirm_s.

        Ask for code only once the card has left the frame, so players read the
        task at their own pace.
        """
        if self._puzzle is None or self._lang is None:
            return
        card_gone = self._clear_run >= self.clear_frames
        if not self._confirmed() or not card_gone:
            self.ui.puzzle_locked(self._puzzle, self._lang)
        else:
            self.ui.await_code(self._puzzle, self._lang, progress, self._code_hint(progress))

    def _code_hint(self, progress: float) -> str:
        """What the player should do next, from what the camera last saw."""
        if self._skips >= SKIPS_BEFORE_HINT:
            return HINT_CLOSER
        last = self.gate.last
        if last.get("blur", self.gate.blur_min) < self.gate.blur_min:
            return HINT_BLURRY
        if progress > 0:
            return HINT_STILL
        return HINT_SHOW

    def _ready_for_code(self, marker) -> bool:
        """The puzzle card is not the code. Confirm it, wait it out, then settle."""
        if marker is not None and marker.marker_id == self._sel_marker_id:
            self._clear_run = 0
            return False
        self._clear_run += 1
        if self._clear_run < self.clear_frames:
            return False
        elapsed = self._now() - self._t_state
        return elapsed >= self.settle_s and elapsed >= self.confirm_s

    def _select_reset(self) -> None:
        self._marker_run_id = None
        self._marker_run = 0
        self._clear_run = 0
        self._stray_id = None
        self._stray_run = 0
        self._skips = 0

    def _to_idle(self) -> None:
        self.state = State.IDLE
        self._puzzle = self._lang = self._sel_marker_id = self._pending = None
        self._pending_image = None
        self._select_reset()
        self.ui.await_card()

    def _cooldown(self) -> None:
        self._cooldown_until = self._now() + self.cooldown_s
