"""Tests for the QR card payload contract (pure Python — runs in CI)."""
import pytest

from vision.cardspec import CardId, encode_payload, parse_payload


def test_round_trip():
    raw = encode_payload(2, "Megad00mer", "python")
    assert parse_payload(raw) == CardId(puzzle=2, team="Megad00mer", lang="python")


def test_round_trip_unicode_and_symbols_in_team():
    # team names are verbatim — the exact thing the VLM got wrong
    raw = encode_payload(3, "Megad@0mer ✦", "bash")
    assert parse_payload(raw) == CardId(puzzle=3, team="Megad@0mer ✦", lang="bash")


@pytest.mark.parametrize("alias,expected", [
    ("py", "python"), ("PYTHON", "python"), ("c++", "cpp"),
    ("C", "cpp"), ("sh", "bash"), (" Bash ", "bash"),
])
def test_lang_aliases_normalized(alias, expected):
    assert parse_payload(encode_payload(1, "t", alias)).lang == expected


def test_encode_rejects_bad_lang():
    with pytest.raises(ValueError):
        encode_payload(1, "team", "rust")


def test_encode_rejects_empty_team():
    with pytest.raises(ValueError):
        encode_payload(1, "   ", "python")


def test_encode_rejects_bool_puzzle():
    # bool is an int subclass — must not slip through as puzzle 1/0
    with pytest.raises(ValueError):
        encode_payload(True, "team", "python")


@pytest.mark.parametrize("raw", [
    "", "not json", "{}", '{"p":1,"t":"x","l":"python"}',          # missing magic
    '{"ocrcade":2,"p":1,"t":"x","l":"python"}',                     # wrong version
    '{"ocrcade":1,"t":"x","l":"python"}',                          # no puzzle
    '{"ocrcade":1,"p":1,"l":"python"}',                            # no team
    '{"ocrcade":1,"p":1,"t":"","l":"python"}',                     # blank team
    '{"ocrcade":1,"p":1,"t":"x","l":"rust"}',                      # bad lang
    '{"ocrcade":1,"p":"two","t":"x","l":"python"}',                # non-int puzzle
    '[1,2,3]',                                                      # not an object
])
def test_parse_rejects_invalid(raw):
    assert parse_payload(raw) is None


def test_parse_tolerates_extra_keys():
    raw = '{"ocrcade":1,"p":4,"t":"Anael","l":"python","future":"ok","nonce":"abc"}'
    assert parse_payload(raw) == CardId(puzzle=4, team="Anael", lang="python")
