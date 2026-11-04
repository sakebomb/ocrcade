"""Pure layout logic behind the Hailo OCR reader: CTC decoding, word cuts, line
grouping and indentation. Stdlib only, so these run in CI without numpy or cv2."""
import pytest

from vision.text_layout import (
    Segment,
    ascii_allowed,
    assemble,
    ctc_decode,
    gap_cuts,
    group_lines,
    join_pieces,
    long_cuts,
    parse_keys,
)

CHARSET = ["blank", "a", "b", "c", " "]


def test_ctc_decode_collapses_repeats_and_drops_blanks():
    text, confs = ctc_decode([1, 1, 0, 1, 2, 2, 0, 0, 3], [0.9] * 9, CHARSET)
    assert text == "aabc"
    assert len(confs) == 4


def test_ctc_decode_keeps_confidence_of_first_step_of_each_char():
    _, confs = ctc_decode([1, 1, 2], [0.5, 0.9, 0.7], CHARSET)
    assert confs == [0.5, 0.7]


def test_ctc_decode_strips_edge_spaces_but_keeps_inner_ones():
    text, confs = ctc_decode([4, 1, 4, 2, 4], [1.0] * 5, CHARSET)
    assert text == "a b"
    assert len(confs) == 3


def test_ascii_allowed_keeps_blank_and_printable_ascii_only():
    assert ascii_allowed(["blank", "a", "~", "中", "\t", "ab", " "]) == [
        True, True, True, False, False, False, True]


def test_parse_keys_accepts_one_trailing_newline_and_keeps_a_space_key():
    assert parse_keys("blank\na\n \n", 3) == ["blank", "a", " "]


def test_parse_keys_rejects_a_file_one_key_short():
    with pytest.raises(ValueError, match="2 classes"):
        parse_keys("blank\na\n", 3)


def test_parse_keys_rejects_a_file_one_key_long():
    with pytest.raises(ValueError, match="4 classes"):
        parse_keys("blank\na\nb\nc", 3)


def test_join_pieces_restores_a_space_only_where_the_cut_was_blank():
    assert join_pieces(["self.value", "+", "other"], [True, False]) == "self.value +other"


def test_join_pieces_skips_empty_pieces_without_doubling_spaces():
    assert join_pieces(["ab", "", "cd"], [True, True]) == "ab cd"
    assert join_pieces([], []) == ""


def test_gap_cuts_splits_only_at_interior_gaps_at_least_min_wide():
    #       ink ink . . . ink . ink ink . . . .
    ink = [1, 1, 0, 0, 0, 1, 0, 1, 1, 0, 0, 0, 0]
    assert gap_cuts(ink, min_gap=3) == [3]


def test_gap_cuts_ignores_leading_and_trailing_blank_columns():
    assert gap_cuts([0, 0, 0, 0, 1, 1, 0, 0, 0, 0], min_gap=2) == []


def test_long_cuts_leaves_a_short_crop_whole():
    assert long_cuts([1.0] * 50, height=10, max_aspect=6) == []


def test_long_cuts_cuts_a_long_crop_at_the_emptiest_column_near_each_target():
    ink = [5.0] * 120
    ink[58] = 0.0  # the only thin column near the middle
    assert long_cuts(ink, height=10, max_aspect=6) == [58]


def test_long_cuts_makes_every_piece_short_enough():
    ink = [1.0] * 400
    cuts = long_cuts(ink, height=10, max_aspect=6)
    edges = [0, *cuts, 400]
    assert all(0 < b - a <= 6 * 10 + 10 for a, b in zip(edges, edges[1:], strict=False))


def seg(top, left, text, h=20.0, width=None):
    width = width if width is not None else 10.0 * len(text)
    return Segment(top=top, bottom=top + h, left=left, right=left + width, text=text)


def test_group_lines_joins_segments_that_overlap_vertically():
    a, b, c = seg(0, 0, "Lang:"), seg(4, 80, "bash"), seg(40, 0, "cat")
    assert group_lines([c, b, a]) == [[a, b], [c]]


def test_group_lines_span_grows_as_a_line_gains_segments():
    a = seg(0, 0, "a", h=20)      # center 10
    b = seg(5, 20, "b", h=30)     # center 20: inside a's span 0-20
    c = seg(25, 40, "c", h=10)    # center 30: inside a+b's span 0-35 only
    assert group_lines([a, b, c]) == [[a, b, c]]


def test_assemble_orders_lines_and_words_and_joins_with_spaces():
    out = assemble([seg(40, 0, "cat"), seg(0, 70, "bash"), seg(2, 0, "Lang:")])
    assert out == "Lang: bash\ncat"


def test_assemble_indents_in_steps_of_four_from_measured_char_width():
    out = assemble([seg(0, 0, "for x in y:"), seg(30, 40, "print(x)")])
    assert out == "for x in y:\n    print(x)"


def test_assemble_snaps_a_near_miss_indent_to_the_nearest_level():
    out = assemble([seg(0, 0, "if a:"), seg(30, 70, "b()")])  # 7 chars -> 8
    assert out == "if a:\n        b()"


def test_assemble_treats_a_tiny_offset_as_no_indent():
    out = assemble([seg(0, 0, "abc"), seg(30, 10, "def")])
    assert out == "abc\ndef"


def test_assemble_ignores_an_implausibly_deep_indent():
    out = assemble([seg(0, 0, "Team: x"), seg(30, 900, "HM")])
    assert out == "Team: x\nHM"


def test_assemble_of_nothing_is_empty():
    assert assemble([]) == ""


def test_segment_rejects_inverted_box():
    with pytest.raises(ValueError):
        Segment(top=10, bottom=5, left=0, right=1, text="x")
