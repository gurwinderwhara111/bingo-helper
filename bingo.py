"""Pure bingo-board math used by the v1.1 power-up guidance.

No OpenCV, no OCR, no Windows code here.  Keeping the line math separate makes
it easy to test and stops UI/capture bugs from changing the bingo answer.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

SIZE = 5


def _row(r: int) -> tuple[tuple[int, int], ...]:
    return tuple((r, c) for c in range(SIZE))


def _col(c: int) -> tuple[tuple[int, int], ...]:
    return tuple((r, c) for r in range(SIZE))


# 5 rows + 5 columns + 2 diagonals + 4 corners = 13 ways to bingo.
# The "corners" line is four cells, not five; that matches the game's help
# screen where the middle free space is shown separately.
LINES: tuple[tuple[str, tuple[tuple[int, int], ...]], ...] = tuple(
    [(f"row {r + 1}", _row(r)) for r in range(SIZE)]
    + [(f"column {c}", _col(c)) for c in range(SIZE)]
    + [("diagonal", tuple((i, i) for i in range(SIZE))),
       ("diagonal", tuple((i, SIZE - 1 - i) for i in range(SIZE))),
       ("four corners", ((0, 0), (0, SIZE - 1), (SIZE - 1, 0), (SIZE - 1, SIZE - 1)))]
)


def initial_daubed(free_cells: Iterable[tuple[int, int]] = ()) -> list[list[bool]]:
    """A fresh round: the centre free space is already marked."""
    d = [[False] * SIZE for _ in range(SIZE)]
    d[2][2] = True
    for r, c in free_cells:
        if 0 <= r < SIZE and 0 <= c < SIZE:
            d[r][c] = True
    return d


def line_counts(daubed: Sequence[Sequence[bool]]) -> list[int]:
    return [sum(1 for r, c in cells if daubed[r][c]) for _, cells in LINES]


def completed_lines(daubed: Sequence[Sequence[bool]]) -> list[str]:
    out = []
    for (name, cells), n in zip(LINES, line_counts(daubed)):
        if n >= len(cells):
            out.append(name)
    return out


def locate_number(values: Sequence[Sequence[Optional[int]]],
                  number: int) -> Optional[tuple[int, int]]:
    for r in range(SIZE):
        for c in range(SIZE):
            if values[r][c] == number:
                return r, c
    return None


@dataclass(frozen=True)
class PickChoice:
    number: int
    row: int
    col: int
    completed: tuple[str, ...]
    one_away: tuple[str, ...]
    two_away: tuple[str, ...]

    @property
    def score(self) -> tuple[int, int, int]:
        return len(self.completed), len(self.one_away), len(self.two_away)


def rank_picks(values: Sequence[Sequence[Optional[int]]],
               daubed: Sequence[Sequence[bool]],
               numbers: Iterable[int]) -> list[PickChoice]:
    """Evaluate every offered number against every bingo line.

    A choice is only considered if the number is on the card and its cell is
    not already daubed.  The returned list is best-first.
    """
    out: list[PickChoice] = []
    seen: set[int] = set()
    for number in numbers:
        if number in seen:
            continue
        seen.add(number)
        pos = locate_number(values, number)
        if pos is None:
            continue
        r, c = pos
        if daubed[r][c]:
            continue

        # 5x5 is tiny; copying is clearer and still effectively free.
        sim = [list(row) for row in daubed]
        sim[r][c] = True
        comp, one, two = [], [], []
        for (name, cells), n in zip(LINES, line_counts(sim)):
            total = len(cells)
            if n >= total:
                comp.append(name)
            elif n == total - 1:
                one.append(name)
            elif n == total - 2:
                two.append(name)
        out.append(PickChoice(number, r, c, tuple(comp), tuple(one), tuple(two)))

    out.sort(key=lambda p: (p.score, p.number), reverse=True)
    return out


def format_choice(choice: PickChoice) -> str:
    """Short line for the control bar."""
    if choice.completed:
        n = len(choice.completed)
        return f"PICK {choice.number} -> {n} BINGO" + ("S" if n != 1 else "")
    if choice.one_away:
        return f"PICK {choice.number} -> 1 AWAY"
    return f"PICK {choice.number} -> setup"


def format_ranking(choices: Sequence[PickChoice]) -> list[str]:
    """Full ranking for the log, one line per choice."""
    lines = []
    for p in choices:
        lines.append(
            f"{p.number:>2} at r{p.row + 1}c{p.col + 1}: "
            f"{len(p.completed)} complete, {len(p.one_away)} one-away, "
            f"{len(p.two_away)} two-away"
        )
    return lines
