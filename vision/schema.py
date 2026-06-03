"""The single structured result every VLM backend returns. Immutable."""
from __future__ import annotations

from dataclasses import dataclass

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

    @classmethod
    def unreadable(cls, reason: str, backend: str = "", latency_s: float = 0.0) -> CardReading:
        return cls(readable=False, reason=reason, backend=backend, latency_s=latency_s)

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
