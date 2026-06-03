"""
Generate a random solvable maze using recursive backtracking (DFS).

Maze characters:
  # = wall
    = path
  S = start (top-left open cell)
  E = end (bottom-right open cell)

Path format: string of R/L/U/D moves from S to E.
"""

import random
from collections import deque


def generate_maze(rows: int = 15, cols: int = 15, seed: int | None = None) -> tuple[list[str], str]:
    """
    Returns (maze_lines, solution_path).
    maze_lines: list of strings, each row of the maze
    solution_path: shortest RLUD path from S to E
    """
    rng = random.Random(seed)

    # Work in cell coordinates (half the grid size)
    cell_rows = rows // 2
    cell_cols = cols // 2

    # Grid of walls: True = wall
    grid = [['#'] * cols for _ in range(rows)]

    def cell_to_grid(cr, cc):
        return cr * 2 + 1, cc * 2 + 1

    def carve(cr, cc, visited):
        visited.add((cr, cc))
        gr, gc = cell_to_grid(cr, cc)
        grid[gr][gc] = ' '
        directions = [(0, 1), (0, -1), (1, 0), (-1, 0)]
        rng.shuffle(directions)
        for dr, dc in directions:
            nr, nc = cr + dr, cc + dc
            if 0 <= nr < cell_rows and 0 <= nc < cell_cols and (nr, nc) not in visited:
                # Carve wall between current and neighbor
                wr, wc = gr + dr, gc + dc
                grid[wr][wc] = ' '
                carve(nr, nc, visited)

    carve(0, 0, set())

    # Place S and E
    sr, sc = cell_to_grid(0, 0)
    er, ec = cell_to_grid(cell_rows - 1, cell_cols - 1)
    grid[sr][sc] = 'S'
    grid[er][ec] = 'E'

    maze = [''.join(row) for row in grid]

    # BFS to find shortest solution
    solution = _bfs_solve(maze, (sr, sc), (er, ec))

    return maze, solution


def _bfs_solve(maze: list[str], start: tuple, end: tuple) -> str:
    MOVES = {'R': (0, 1), 'L': (0, -1), 'U': (-1, 0), 'D': (1, 0)}
    rows = len(maze)
    cols = max(len(r) for r in maze)

    queue = deque([(start, '')])
    visited = {start}

    while queue:
        (r, c), path = queue.popleft()
        if (r, c) == end:
            return path
        for move, (dr, dc) in MOVES.items():
            nr, nc = r + dr, c + dc
            if (nr, nc) not in visited and 0 <= nr < rows and 0 <= nc < cols:
                cell = maze[nr][nc] if nc < len(maze[nr]) else '#'
                if cell != '#':
                    visited.add((nr, nc))
                    queue.append(((nr, nc), path + move))

    return ''  # no solution (shouldn't happen with valid maze)


def maze_to_string(maze: list[str]) -> str:
    return '\n'.join(maze)


if __name__ == '__main__':
    # Quick test
    maze, solution = generate_maze(15, 15)
    print(maze_to_string(maze))
    print(f"\nSolution ({len(solution)} steps): {solution}")
