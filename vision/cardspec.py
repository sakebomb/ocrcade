"""OCRcade card QR payload — the single source of truth for a card's identity.

Identity (puzzle / team / lang) comes from a QR code on the card, NOT from the
VLM reading the header. This is deliberate: the VLM is excellent at transcribing
the code body but misreads small header text (team names like "Megad00mer" vs
"Megadd0mer") and can misroute the puzzle. A QR decodes locally, instantly, and
deterministically — so the VLM only has to do the one thing it is good at.

Payload format (JSON, magic-keyed so we ignore unrelated QRs in frame):

    {"ocrcade": 1, "p": <puzzle:int>, "t": "<team>", "l": "python|bash|cpp"}

`p` is the puzzle number, `t` the team name (verbatim, any characters), `l` the
language. Extra keys are tolerated for forward-compat. Both the encoder
(``tools/card_generator.html``, mirrored here for the Python side) and the
decoder (``vision/qr.py``) go through this module so the format lives in one
place.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

MAGIC_KEY = "ocrcade"
PAYLOAD_VERSION = 1
SUPPORTED_LANGS = ("python", "bash", "cpp")

_LANG_ALIASES = {"sh": "bash", "c++": "cpp", "c": "cpp", "py": "python"}


@dataclass(frozen=True)
class CardId:
    """Validated card identity decoded from a QR payload. Immutable."""
    puzzle: int
    team: str
    lang: str


def encode_payload(puzzle: int, team: str, lang: str) -> str:
    """Build the canonical QR payload string. Mirrors the HTML generator.

    Raises ValueError on invalid input so a bad card can't be generated.
    """
    lang_norm = _normalize_lang(lang)
    if lang_norm is None:
        raise ValueError(f"lang must be one of {SUPPORTED_LANGS}, got {lang!r}")
    team_clean = (team or "").strip()
    if not team_clean:
        raise ValueError("team must be non-empty")
    if not isinstance(puzzle, int) or isinstance(puzzle, bool):
        raise ValueError(f"puzzle must be an int, got {puzzle!r}")
    # compact, stable key order; ensure_ascii=False keeps unicode team names intact
    return json.dumps(
        {MAGIC_KEY: PAYLOAD_VERSION, "p": puzzle, "t": team_clean, "l": lang_norm},
        ensure_ascii=False, separators=(",", ":"),
    )


def parse_payload(raw: str) -> CardId | None:
    """Decode a QR payload string into a CardId, or None if it is not a valid
    OCRcade card payload. Never raises — unknown/garbage QRs simply return None
    so the trigger can ignore them."""
    if not raw:
        return None
    try:
        d = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(d, dict) or d.get(MAGIC_KEY) != PAYLOAD_VERSION:
        return None

    puzzle = d.get("p")
    if puzzle is None:
        return None
    try:
        puzzle = int(puzzle)
    except (TypeError, ValueError):
        return None

    team = d.get("t")
    if not isinstance(team, str) or not team.strip():
        return None

    lang = _normalize_lang(d.get("l"))
    if lang is None:
        return None

    return CardId(puzzle=puzzle, team=team.strip(), lang=lang)


def _normalize_lang(lang: object) -> str | None:
    if not isinstance(lang, str):
        return None
    norm = _LANG_ALIASES.get(lang.strip().lower(), lang.strip().lower())
    return norm if norm in SUPPORTED_LANGS else None
