#!/usr/bin/env python3
"""
Sync team list from RootTheBox into teams.json.

For each team not yet in teams.json, this script:
  1. Scrapes /admin/users for the team's join code
  2. Registers a bot user account on that team via /registration
  3. Saves the bot credentials to teams.json

Run periodically so new teams are picked up automatically:
    */2 * * * * /path/to/venv/bin/python /path/to/sync_teams.py >> /var/log/ocrcade-sync.log 2>&1
"""

import json
import logging
import os
import re
import secrets
import sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# RTB connection comes from the environment — never hardcode credentials.
RTB_HOST     = os.environ.get('OCRCADE_RTB_HOST', 'http://127.0.0.1:8888')
RTB_HANDLE   = os.environ.get('OCRCADE_RTB_ADMIN_USER', 'admin')
RTB_PASS     = os.environ.get('OCRCADE_RTB_ADMIN_PASSWORD', '')
# TLS: CA bundle if provided, else unverified (RTB self-signed). Matches rtb_submitter.
_RTB_CA      = os.environ.get('OCRCADE_RTB_CA_BUNDLE', '')
RTB_VERIFY   = _RTB_CA if _RTB_CA else False
TEAMS_FILE   = Path(__file__).resolve().parent.parent / 'teams.json'

log = logging.getLogger('ocrcade.sync')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)-8s  %(message)s',
)


# ── XSRF helper ──────────────────────────────────────────────────────────────

def _xsrf(session: requests.Session) -> str:
    """Return the current _xsrf cookie value from the session jar."""
    return session.cookies.get('_xsrf', '') or ''


def _post(session: requests.Session, url: str, data: dict, **kwargs) -> requests.Response:
    """POST with Tornado's XSRF token included."""
    return session.post(url, data={**data, '_xsrf': _xsrf(session)}, **kwargs)


# ── Admin login ───────────────────────────────────────────────────────────────

def admin_login() -> requests.Session:
    if not RTB_PASS:
        log.error('OCRCADE_RTB_ADMIN_PASSWORD is not set — export it before running.')
        sys.exit(1)
    session = requests.Session()
    session.verify = RTB_VERIFY
    session.get(f'{RTB_HOST}/login', timeout=10)   # grab _xsrf cookie
    resp = _post(session, f'{RTB_HOST}/login', {
        'account':  RTB_HANDLE,
        'password': RTB_PASS,
    }, timeout=10, allow_redirects=True)
    if 'logout' not in resp.text.lower() and 'dashboard' not in resp.text.lower():
        log.error('RTB admin login failed – check credentials/host')
        sys.exit(1)
    log.info('Admin session established')
    return session


# ── Parse admin/users page ────────────────────────────────────────────────────

def fetch_rtb_teams(session: requests.Session) -> list[dict]:
    """
    Returns list of {name, uuid, code} dicts for every team on /admin/users.
    """
    resp = session.get(f'{RTB_HOST}/admin/users', timeout=10)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, 'html.parser')
    teams = []

    for well in soup.find_all('div', class_='well'):
        h2 = well.find('h2')
        if not h2:
            continue
        name = h2.get_text(separator='').replace('\xa0', '').strip()
        if not name:
            continue

        # UUID is on the edit/delete buttons as data-uuid
        btn = well.find(attrs={'data-uuid': True})
        uuid = btn['data-uuid'] if btn else None

        # Team code follows "Team Code:" bold label
        code_tag = well.find('strong', string=re.compile(r'Team\s+Code', re.I))  # type: ignore[call-overload]
        code = None
        if code_tag and code_tag.next_sibling:
            code = str(code_tag.next_sibling).strip().lstrip(':').strip()

        if uuid and code:
            teams.append({'name': name, 'uuid': uuid, 'code': code})
        else:
            log.warning(f'Could not parse UUID/code for team {name!r} – skipping')

    return teams


# ── Bot user registration ─────────────────────────────────────────────────────

def _bot_handle(team_name: str) -> str:
    """
    Derive a unique-enough RTB handle for the bot account.
    RTB handles: 3–16 chars, no spaces.
    """
    slug = re.sub(r'[^a-z0-9]', '', team_name.lower())[:12]
    if len(slug) < 1:
        slug = 'team'
    return f'{slug}_bot'


def register_bot(team_code: str, handle: str, password: str) -> bool:
    """
    Register a new bot user and join the given team via team code.
    Uses a fresh unauthenticated session so the XSRF token is valid for /registration.
    Returns True on success.
    """
    fresh = requests.Session()
    fresh.verify = RTB_VERIFY
    fresh.get(f'{RTB_HOST}/registration', timeout=10)

    resp = _post(fresh, f'{RTB_HOST}/registration', {
        'handle':     handle,
        'playername': re.sub(r'[^a-z0-9]', '', handle),  # no underscores allowed
        'teammode':   'join',
        'team-code':  team_code,
        'pass1':      password,
        'pass2':      password,
    }, timeout=10, allow_redirects=True)

    # RTB returns 200+form regardless of success/failure.
    # Verify by attempting login with the new credentials.
    verify = requests.Session()
    verify.verify = RTB_VERIFY
    verify.get(f'{RTB_HOST}/login', timeout=10)
    vxsrf = verify.cookies.get('_xsrf', '')
    vresp = verify.post(f'{RTB_HOST}/login', data={
        'account': handle, 'password': password, '_xsrf': vxsrf,
    }, timeout=10, allow_redirects=True)
    vtext = vresp.text.lower()
    if 'logout' in vtext or 'missions' in vtext or 'profile' in vtext:
        return True
    # Already-registered case: the handle exists but we don't have the password
    if 'already' in resp.text.lower() or 'registered' in resp.text.lower():
        log.warning(f'Handle {handle!r} exists with unknown password – re-register manually')
    log.warning(f'Registration or login failed for {handle!r} – status {resp.status_code}')
    return False


# ── Main sync ─────────────────────────────────────────────────────────────────

def sync() -> None:
    existing: dict = {}
    if TEAMS_FILE.exists():
        existing = json.loads(TEAMS_FILE.read_text())

    session = admin_login()
    rtb_teams = fetch_rtb_teams(session)

    if not rtb_teams:
        log.warning('No teams found – HTML parsing may need adjustment')
        return

    added = updated = 0
    for team in rtb_teams:
        name = team['name']
        uuid = team['uuid']
        code = team['code']

        if name in existing:
            if existing[name].get('uuid') != uuid:
                existing[name]['uuid'] = uuid
                updated += 1
                log.info(f'Updated UUID for {name!r}')
            continue

        # New team – create a bot account
        handle   = _bot_handle(name)
        password = secrets.token_urlsafe(16)

        log.info(f'Registering bot for new team {name!r} – handle={handle!r}')
        ok = register_bot(code, handle, password)

        if ok:
            existing[name] = {
                'uuid':         uuid,
                'bot_handle':   handle,
                'bot_password': password,
            }
            added += 1
        else:
            log.error(f'Bot registration failed for team {name!r}')

    TEAMS_FILE.write_text(json.dumps(existing, indent=2))
    log.info(f'Sync complete – {len(rtb_teams)} RTB teams, {added} added, {updated} updated')


if __name__ == '__main__':
    sync()
