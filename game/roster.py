"""Team identity from the code body.

In the ArUco model the puzzle/lang come from a physical marker card; the TEAM
name is written in the player's code as an inert comment:

    # team: Megad00mer      (python / bash)
    // team: Megad00mer     (cpp)

The VLM transcribes that line like any other code, so it can be misread the same
way it misread headers before ("Megad00mer" -> "Megadd0mer"). We defend against
that by fuzzy-matching the extracted name to the PRE-REGISTERED roster: the
candidate set is tiny, so a one-character OCR slip still resolves to the right
team. Pure stdlib (difflib) — no heavy deps, runs in CI.
"""
from __future__ import annotations

import difflib
import re

# `# team: x`, `// team: x`, `#team=x`, case-insensitive, flexible whitespace.
_TEAM_LINE = re.compile(r"""(?:\#|//)\s*team\s*[:=]\s*(\S.*?)\s*$""",
                        re.IGNORECASE | re.MULTILINE)


def extract_team_line(code: str | None) -> str | None:
    """Return the raw team name from the first `# team: ...` line, or None."""
    if not code:
        return None
    m = _TEAM_LINE.search(code)
    return m.group(1).strip() if m else None


def match_team(name: str | None, roster: list[str], cutoff: float = 0.6) -> str | None:
    """Fuzzy-match a (possibly misread) team name to the registered roster.

    Returns the canonical roster spelling, or None if nothing is close enough.
    Case-insensitive matching, but the roster's original spelling is returned.
    """
    if not name or not roster:
        return None
    name_norm = name.strip().lower()
    if not name_norm:
        return None
    # exact (case-insensitive) wins immediately
    by_lower = {t.lower(): t for t in roster}
    if name_norm in by_lower:
        return by_lower[name_norm]
    hits = difflib.get_close_matches(name_norm, list(by_lower.keys()), n=1, cutoff=cutoff)
    return by_lower[hits[0]] if hits else None


def resolve_team(code: str | None, roster: list[str], cutoff: float = 0.6) -> str | None:
    """Extract the team line from code and resolve it to a team name.

    With a roster configured, fuzzy-match to it (recovers OCR slips, rejects
    unregistered teams). With NO roster configured (empty), the feature is treated
    as off — accept the read name verbatim so the game still works out-of-the-box.
    """
    name = extract_team_line(code)
    if not roster:
        return name
    return match_team(name, roster, cutoff=cutoff)
