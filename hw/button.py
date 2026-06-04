"""Physical confirm button (Phase C) — a pollable ConfirmSource for the pipeline.

A momentary button wired between a GPIO pin and GND, internal pull-up, active-low:
released reads 1, pressed reads 0. `poll()` returns "go" on a fresh press edge
(a 1→0 transition) seen after `arm()`; the pipeline's read-back timeout handles
the cancel case. The GPIO level reader is injectable so this unit-tests without
hardware; on the Pi 5 it uses lgpio (RP1-correct; RPi.GPIO is unreliable there).
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable

log = logging.getLogger("ocrcade.button")


def lgpio_reader(pin: int = 17, chip: int = 0) -> Callable[[], int]:
    """Build a level reader for a pull-up input on `pin` (BCM). Pressed = 0."""
    import lgpio

    handle = lgpio.gpiochip_open(chip)
    lgpio.gpio_claim_input(handle, pin, lgpio.SET_PULL_UP)
    log.info(f"button: lgpio chip{chip} GPIO{pin} (pull-up, active-low)")
    return lambda: lgpio.gpio_read(handle, pin)


class ButtonConfirm:
    """ConfirmSource backed by a momentary GPIO button (active-low)."""

    def __init__(self, pin: int = 17, chip: int = 0,
                 read_level: Callable[[], int] | None = None,
                 debounce_s: float = 0.04, now: Callable[[], float] = time.monotonic):
        self._read = read_level if read_level is not None else lgpio_reader(pin, chip)
        self._debounce_s = debounce_s
        self._now = now
        self._last = 1                    # assume released
        self._last_edge = float("-inf")   # so the first press is never debounced away

    def arm(self) -> None:
        # snapshot current level so a button already-held at arm time doesn't
        # instantly count as a press; we wait for the next fresh 1→0 edge.
        self._last = self._read()

    def poll(self) -> str | None:
        level = self._read()
        pressed_edge = (self._last == 1 and level == 0)
        self._last = level
        if pressed_edge and (self._now() - self._last_edge) > self._debounce_s:
            self._last_edge = self._now()
            return "go"
        return None
