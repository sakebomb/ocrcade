"""Reading-accuracy metrics used by tools/eval_reader.py (pure, runs in CI)."""
from evaluation.metrics import cer, exact_match, levenshtein, normalize


def test_normalize_drops_trailing_space_and_blank_edges():
    assert normalize("\n\nx = 1   \r\n  y = 2\t\n\n") == "x = 1\n  y = 2"


def test_normalize_turns_windows_line_endings_into_newlines():
    assert normalize("a\r\nb") == "a\nb"
    assert "\r" not in normalize("a\r\n\r\nb")


def test_normalize_expands_tabs_to_four_spaces():
    assert normalize("for i in x:\n\tprint(i)") == "for i in x:\n    print(i)"


def test_levenshtein_counts_edits():
    assert levenshtein("kitten", "sitting") == 3
    assert levenshtein("", "abc") == 3
    assert levenshtein("same", "same") == 0


def test_cer_is_zero_for_identical_code():
    assert cer("print(1)\n", "print(1)") == 0.0


def test_cer_counts_one_substitution_over_truth_length():
    assert cer("open", "opan") == 0.25


def test_cer_is_one_when_nothing_was_read():
    assert cer("print(1)", None) == 1.0
    assert cer("print(1)", "") == 1.0


def test_exact_match_ignores_trailing_space_but_not_indentation():
    assert exact_match("if x:\n    y()", "if x:   \n    y()\n")
    assert not exact_match("if x:\n    y()", "if x:\n  y()")
    assert not exact_match("x", None)
