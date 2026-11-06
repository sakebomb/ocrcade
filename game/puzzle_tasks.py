"""What each puzzle asks the player to do: the question, never the answer.

The vision daemon shows these lines while a puzzle card is in front of the camera.
"""
from __future__ import annotations

TASKS: dict[int, tuple[str, ...]] = {
    0: ("Print anything. Proves the station works.",),
    1: ("Your program can read the file /puzzle/flag1.txt.",
        "Print what is inside it."),
    2: ("/puzzle/data.txt holds a list of numbers.",
        "Add them all up and print the total."),
    3: ("/puzzle/maze_fixed.txt is a maze. Walk it from S to E",
        "and print your moves as letters: R L U D."),
    4: ("Same idea, but the maze changes every time.",
        "Print the path from S to E as R L U D."),
    5: ("No code. Keep this card up and do the Konami code",
        "with your hands for the camera."),
    6: ("No code. Keep this card up and slice the fruit with",
        "your hands."),
}

CAMERA_PUZZLES = (5, 6)


def task_lines(puzzle: int) -> list[str]:
    """The task, then what to do next. Empty task for an unknown puzzle."""
    lines = list(TASKS.get(puzzle, ()))
    if puzzle in CAMERA_PUZZLES:
        lines.append("Keep the card up. Starting in a moment...")
    else:
        lines.append("Take the card away, then show your code.")
    return lines
