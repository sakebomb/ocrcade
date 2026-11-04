"""Parsing the Hailo VLM's plain-text transcription (pure, runs in CI)."""
from evaluation.parse import parse_transcription


def test_plain_code_passes_through():
    assert parse_transcription("x = 1\nprint(x)") == ("x = 1\nprint(x)", "")


def test_end_token_and_code_fences_are_stripped():
    raw = "```python\nprint(1)\n```<|im_end|>"
    assert parse_transcription(raw) == ("print(1)", "")


def test_endoftext_token_is_stripped():
    assert parse_transcription("print(1)<|endoftext|>") == ("print(1)", "")


def test_unterminated_fence_from_a_truncated_read_is_stripped():
    assert parse_transcription("```python\nprint(1)\nfor x in") == ("print(1)\nfor x in", "")


def test_first_line_indentation_is_kept():
    assert parse_transcription("    return 1\n") == ("    return 1", "")


def test_unreadable_marker_abstains():
    text, reason = parse_transcription("UNREADABLE<|im_end|>")
    assert text is None and reason == "model abstained"


def test_unreadable_marker_abstains_in_any_case_and_with_a_period():
    for raw in ("unreadable", "Unreadable."):
        assert parse_transcription(raw) == (None, "model abstained")


def test_empty_output_abstains():
    text, reason = parse_transcription("  \n<|im_end|>")
    assert text is None and reason == "empty output"


def test_none_from_a_failed_call_abstains():
    assert parse_transcription(None) == (None, "no output")
