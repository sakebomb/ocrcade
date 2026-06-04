"""
Submit flag captures to RootTheBox on behalf of teams.

Each team has a bot account registered by sync_teams.py.
This module logs in as that bot, submits the flag token to RTB,
and RTB records the capture + awards the configured points.

Flag UUIDs must be filled in flag_config.json before the event.
"""

import json
import logging
import os
import re
import threading
from difflib import get_close_matches
from pathlib import Path

import requests

log = logging.getLogger('ocrcade.rtb')

# RTB target comes from the environment — never hardcode host/box identifiers.
RTB_HOST        = os.environ.get('OCRCADE_RTB_HOST', 'https://127.0.0.1:8888')
ORCADE_BOX_UUID = os.environ.get('OCRCADE_RTB_BOX_UUID', '')
# TLS verification: point OCRCADE_RTB_CA_BUNDLE at the RTB CA cert to verify properly.
# Unset → no verification (RTB ships a self-signed cert), with a one-time warning.
_RTB_CA   = os.environ.get('OCRCADE_RTB_CA_BUNDLE', '')
RTB_VERIFY = _RTB_CA if _RTB_CA else False
if not RTB_VERIFY:
    log.warning('RTB TLS verification disabled — set OCRCADE_RTB_CA_BUNDLE to enable')
_ROOT           = Path(__file__).resolve().parent.parent
TEAMS_FILE      = _ROOT / 'teams.json'
FLAGS_DIR      = _ROOT / 'flags'
FLAG_CONFIG     = _ROOT / 'flag_config.json'

# Per-team session cache: {team_name: requests.Session}
_sessions: dict[str, requests.Session] = {}
_lock = threading.Lock()


# ── XSRF helper ──────────────────────────────────────────────────────────────

def _xsrf(session: requests.Session) -> str:
    return session.cookies.get('_xsrf', '') or ''


def _post(session: requests.Session, url: str, data: dict, **kwargs) -> requests.Response:
    return session.post(url, data={**data, '_xsrf': _xsrf(session)}, **kwargs)


# ── Config loading ────────────────────────────────────────────────────────────

def _load_teams() -> dict:
    if not TEAMS_FILE.exists():
        log.error('teams.json not found – run sync_teams.py first')
        return {}
    return json.loads(TEAMS_FILE.read_text())


def _load_flag_config() -> dict:
    if not FLAG_CONFIG.exists():
        log.error('flag_config.json not found')
        return {}
    return json.loads(FLAG_CONFIG.read_text())


def _read_flag_token(puzzle: int) -> str:
    return (FLAGS_DIR / f'flag{puzzle}.txt').read_text().strip()


# ── Session management ────────────────────────────────────────────────────────

def _login(handle: str, password: str) -> requests.Session | None:
    session = requests.Session()
    session.verify = RTB_VERIFY   # CA bundle if provided, else unverified (self-signed)
    session.get(f'{RTB_HOST}/login', timeout=10)   # grab _xsrf cookie
    resp = _post(session, f'{RTB_HOST}/login', {
        'account':  handle,
        'password': password,
    }, timeout=10, allow_redirects=True)
    text = resp.text.lower()
    if 'logout' in text or 'missions' in text or 'profile' in text:
        log.debug(f'Bot login OK: {handle!r}')
        return session
    log.error(f'Bot login failed for {handle!r} – check credentials in teams.json')
    return None


def _get_session(team_name: str, handle: str, password: str) -> requests.Session | None:
    with _lock:
        if team_name not in _sessions:
            s = _login(handle, password)
            if s:
                _sessions[team_name] = s
        return _sessions.get(team_name)


def _invalidate_session(team_name: str) -> None:
    with _lock:
        _sessions.pop(team_name, None)


# ── Flag submission ───────────────────────────────────────────────────────────

def _submit_flag(session: requests.Session, flag_uuid: str, flag_token: str) -> requests.Response:
    # GET the box missions page to ensure fresh _xsrf (box UUID in URL)
    session.get(f'{RTB_HOST}/user/missions/boxes?uuid={ORCADE_BOX_UUID}', timeout=10)
    # POST to box URL: box UUID in query param, flag UUID in body
    return _post(session, f'{RTB_HOST}/user/missions/boxes?uuid={ORCADE_BOX_UUID}', {
        'uuid':  flag_uuid,
        'token': flag_token,
    }, timeout=10, allow_redirects=True)


def _parse_result(resp: requests.Response) -> tuple[bool, str]:
    text = resp.text.lower()
    if 'answered correctly' in text or 'points added' in text or 'has been added' in text:
        return True, 'Flag captured successfully'
    if 'already' in text and ('captured' in text or 'answered' in text):
        return True, 'Flag already captured by this team'
    return False, f'RTB submission unclear (status {resp.status_code}) – check RTB manually'


# ── Solved tracking ──────────────────────────────────────────────────────────

def is_solved(team_name: str, puzzle: int) -> bool:
    """Return True if this team has already captured this puzzle's flag."""
    teams = _load_teams()
    team_key = next((k for k in teams if k.lower() == team_name.lower()), None)
    if not team_key:
        return False
    return puzzle in teams[team_key].get('solved', [])


def _mark_solved(team_key: str, puzzle: int) -> None:
    """Persist puzzle as solved for this team in teams.json."""
    with _lock:
        teams = _load_teams()
        if team_key not in teams:
            return
        solved = teams[team_key].setdefault('solved', [])
        if puzzle not in solved:
            solved.append(puzzle)
            TEAMS_FILE.write_text(json.dumps(teams, indent=2))


# ── Team name resolution ──────────────────────────────────────────────────────

_NOISE_RE = re.compile(r'[^A-Za-z0-9_\-]')


def _o_zero_variants(name: str) -> list[str]:
    """Generate OCR-correction variants of a team name.

    Handles:
    - O ↔ 0 confusion
    - | → l (pipe misread as lowercase L)
    - Spaces injected mid-name by OCR (e.g. 'me gad¥Omer' → 'megad0mer')
    - Noise characters (¥, @, |, etc.) inserted by OCR
    """
    pipe_fixed = name.replace('|', 'l')         # | misread as l (e.g. 'Ange|' → 'Angel')
    no_spaces  = name.replace(' ', '')           # spaces OCR inserts mid-name
    clean      = _NOISE_RE.sub('', name)         # only keep alphanumeric + _ -

    seen: list[str] = []
    for base in (pipe_fixed, name, no_spaces, clean):
        if not base:
            continue
        for v in (
            base,
            base.replace('O', '0').replace('o', '0'),   # letters → digits
            base.replace('0', 'O'),                      # digits → letters
        ):
            if v and v not in seen:
                seen.append(v)
    return seen


def _resolve_team(team_name: str, known_teams: list[str]) -> str | None:
    """Find the best-matching known team name, tolerating O/0 OCR confusion."""
    for variant in _o_zero_variants(team_name):
        # Exact (case-insensitive)
        exact = next((k for k in known_teams if k.lower() == variant.lower()), None)
        if exact:
            if variant != team_name:
                log.info(f"O/0 corrected team '{team_name}' → '{exact}'")
            return exact
        # Fuzzy
        close = get_close_matches(variant, known_teams, n=1, cutoff=0.6)
        if close:
            log.info(f"Fuzzy matched team '{team_name}' (variant '{variant}') → '{close[0]}'")
            return close[0]
    return None


# ── Public API ────────────────────────────────────────────────────────────────

def submit(team_name: str, puzzle: int, flag: str = '') -> tuple[bool, str]:
    """
    Submit a flag capture to RTB for the given team and puzzle.
    Returns (success, message).

    `flag` param is unused – token comes from flags/ directory.
    """
    teams      = _load_teams()
    flag_cfg   = _load_flag_config()

    # Resolve team — exact match first, then fuzzy with O↔0 variants (handles OCR confusion)
    team_key = _resolve_team(team_name, list(teams.keys()))
    if not team_key:
        msg = f"Team '{team_name}' not in teams.json (closest match not found)"
        log.warning(msg)
        return False, msg

    team_data = teams[team_key]
    handle    = team_data.get('bot_handle')
    password  = team_data.get('bot_password')

    if not handle or not password:
        msg = f"No bot credentials for team '{team_name}' – run sync_teams.py"
        log.warning(msg)
        return False, msg

    flag_uuid = flag_cfg.get(str(puzzle))
    if not flag_uuid or flag_uuid.startswith('REPLACE'):
        msg = f'Flag UUID for puzzle {puzzle} not set in flag_config.json'
        log.warning(msg)
        return False, msg

    try:
        flag_token = _read_flag_token(puzzle)
    except FileNotFoundError:
        msg = f'Flag file for puzzle {puzzle} not found'
        log.error(msg)
        return False, msg

    session = _get_session(team_key, handle, password)
    if not session:
        return False, f"Could not log in as bot for team '{team_name}'"

    try:
        resp = _submit_flag(session, flag_uuid, flag_token)
    except requests.RequestException as e:
        msg = f'RTB request failed: {e}'
        log.error(msg)
        return False, msg

    # Detect session expiry (redirect to /login)
    if resp.status_code in (301, 302) and '/login' in resp.headers.get('Location', ''):
        log.info(f'Session expired for {team_name!r}, re-authenticating')
        _invalidate_session(team_key)
        session = _get_session(team_key, handle, password)
        if not session:
            return False, f"Re-auth failed for team '{team_name}'"
        try:
            resp = _submit_flag(session, flag_uuid, flag_token)
        except requests.RequestException as e:
            return False, f'RTB request failed after re-auth: {e}'

    success, msg = _parse_result(resp)
    if success:
        log.info(f"Puzzle {puzzle} flag captured for team '{team_name}'")
        _mark_solved(team_key, puzzle)
    else:
        log.warning(f"Puzzle {puzzle} submission unclear for team '{team_name}': {msg}")
    return success, msg
