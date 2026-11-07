"""Reference solutions that people copy by hand to build a labelled set.

Each is the truth for its samples. They are short enough to copy, and between them they
cover what goes wrong in handwriting: indentation (2), quotes and nested brackets (1, 2),
and runs of look-alike letters (3). Puzzle 4 has none: its maze changes each reset
(and is not shown to players yet, #17).
"""
from __future__ import annotations

REFERENCES: dict[int, str] = {
    0: '# team: ocrcade\nprint("hello")\n',
    1: '# team: ocrcade\nprint(open("/puzzle/flag1.txt").read())\n',
    2: (
        "# team: ocrcade\n"
        "total = 0\n"
        'for line in open("/puzzle/data.txt"):\n'
        "    total += int(line)\n"
        "print(total)\n"
    ),
    # One path, three short lines: Python joins adjacent strings, so it still prints
    # on one line, and each line stays large enough to read on the answer slip.
    3: (
        "# team: ocrcade\n"
        'print("DDDDRRUURRUURR"\n'
        '      "DDRRUURRDDDDRR"\n'
        '      "DDDDDDLLDDRR")\n'
    ),
}

PUZZLES = tuple(sorted(REFERENCES))


def reference(puzzle: int) -> str:
    return REFERENCES[puzzle]
