"""Tests for team extraction + roster fuzzy-match (pure stdlib — runs in CI)."""
import pytest

from game.roster import extract_team_line, match_team, resolve_team

ROSTER = ["Megad00mer", "Anael", "Team Rocket", "byte_me", "Angel"]


@pytest.mark.parametrize("code,expected", [
    ("# team: Megad00mer\nprint(1)", "Megad00mer"),
    ("// team: Anael\nint main(){}", "Anael"),
    ("#team=byte_me", "byte_me"),
    ("print(1)\n#   Team :   Team Rocket  ", "Team Rocket"),
    ("PRINT(1)\n# TEAM: Anael", "Anael"),
])
def test_extract_team_line(code, expected):
    assert extract_team_line(code) == expected


@pytest.mark.parametrize("code", ["print(1)", "", None, "# not a team line"])
def test_extract_returns_none_when_absent(code):
    assert extract_team_line(code) is None


def test_extract_takes_first_team_line():
    assert extract_team_line("# team: Anael\n# team: Megad00mer") == "Anael"


def test_match_exact_case_insensitive():
    assert match_team("megad00mer", ROSTER) == "Megad00mer"  # canonical spelling back


def test_match_recovers_ocr_slip():
    # the exact failure we're defending against: one char misread
    assert match_team("Megadd0mer", ROSTER) == "Megad00mer"
    assert match_team("Megad0Omer", ROSTER) == "Megad00mer"


def test_match_returns_none_when_nothing_close():
    assert match_team("Ztotallydifferent", ROSTER) is None


def test_match_none_inputs():
    assert match_team(None, ROSTER) is None
    assert match_team("Anael", []) is None
    assert match_team("   ", ROSTER) is None


def test_close_names_still_distinguished():
    # "Anael" vs "Angel" are both in the roster; an exact-ish read must not collapse
    assert match_team("Anael", ROSTER) == "Anael"
    assert match_team("Angel", ROSTER) == "Angel"


def test_resolve_team_end_to_end():
    code = "# team: Megadd0mer\nwith open('data.txt') as f:\n    print(sum(int(x) for x in f))"
    assert resolve_team(code, ROSTER) == "Megad00mer"


def test_resolve_team_no_roster_is_verbatim():
    # unconfigured roster → feature off → accept the read name as-is
    assert resolve_team("# team: Whoever\nprint(1)", []) == "Whoever"
    assert resolve_team("print(1)", []) is None        # still need a team line
