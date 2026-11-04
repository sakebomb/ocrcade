"""The single structured result every VLM backend returns. Immutable."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

_FENCE = re.compile(r"^\s*```[a-zA-Z]*\s*\n?(.*?)\n?\s*```\s*$", re.DOTALL)

SUPPORTED_LANGS = ("python", "bash", "cpp")


@dataclass(frozen=True)
class CardReading:
    """A read of one OCRcade card.

    `readable` is the gate: if False, nothing downstream trusts the other fields
    (this is how we refuse to submit a hallucinated read of a blank/garbage frame).
    """
    readable: bool
    puzzle: int | None = None
    team: str | None = None
    lang: str | None = None
    code: str | None = None
    reason: str = ""          # why unreadable, or backend notes
    backend: str = ""         # which backend produced this
    latency_s: float = 0.0
    failed: bool = False      # the backend broke (unreachable, not loaded, bad output),
                              # as opposed to looking and declining; another may retry

    @classmethod
    def unreadable(cls, reason: str, backend: str = "", latency_s: float = 0.0) -> CardReading:
        """The reader looked at the frame and could not read it: final."""
        return cls(readable=False, reason=reason, backend=backend, latency_s=latency_s)

    @classmethod
    def failure(cls, reason: str, backend: str = "", latency_s: float = 0.0) -> CardReading:
        """The reader could not do its job at all; the client may try another."""
        return cls(readable=False, reason=reason, backend=backend, latency_s=latency_s,
                   failed=True)

    def is_camera_challenge(self) -> bool:
        """Puzzles 5 (Konami) and 6 (Fruit Ninja) need no code body."""
        return self.puzzle in (5, 6)

    def is_complete_code_card(self) -> bool:
        """A code puzzle we can actually submit: header + non-empty code."""
        return bool(
            self.readable
            and self.puzzle is not None
            and self.team
            and self.lang in SUPPORTED_LANGS
            and self.code
            and self.code.strip()
        )


def code_reading_from_json(raw: str, backend: str, latency_s: float) -> CardReading:
    """Turn a model's answer to READ_CODE_PROMPT into a CardReading. An explicit
    readable:false is the model declining (final); anything malformed is a failure."""
    m = _FENCE.match(raw)
    text = m.group(1) if m else raw
    try:
        d = json.loads(text)
    except json.JSONDecodeError:
        return CardReading.failure(f"non-JSON: {raw[:120]}", backend, latency_s)
    if not isinstance(d, dict):
        return CardReading.failure(f"non-object JSON: {raw[:120]}", backend, latency_s)
    if not d.get("readable", False):
        return CardReading.unreadable(str(d.get("reason", "model abstained")), backend, latency_s)
    code = d.get("code")
    if not isinstance(code, str) or not code.strip():
        return CardReading.failure("readable but no code", backend, latency_s)
    return CardReading(readable=True, code=code, backend=backend, latency_s=latency_s)
