"""Cells → a raw HTML `<table>` whose grid is rectangular, with the range of every cell's text in it.

Every converter that writes a table with merged cells from a cell list (docling's `TableCell`s, a model's reply)
goes through `html_table`. WHY one builder: a cell list from a layout model is not always a grid. Mean Well's
LRS-350 sheet (docling, 2026-10-29) has no cell at row 0, col 1 — "MODEL" sits in col 0 while the label columns
below are col 0 (the OUTPUT group, rowspan 11) and col 1 — and writing the cells row by row put every model name one
column left of its values: 350.4 W read as LRS-350-36's rating. The index was right; the markdown corrupted it.

The grid it writes:
- every cell starts at exactly its own (row, col), so the column a reader sees is the column the index says;
- a slot no cell covers becomes an empty `<td></td>`.
  WHY an empty filler, not a wider neighbour: the cell list says nothing sits there, and docling's box for
  "MODEL" ends inside col 0, so nothing in the data says the source merged it. A guessed colspan would make a
  header claim a column it may not label — the same corruption as a shifted header; an empty cell asserts nothing.
- a span that would cover another cell's start, or a slot an earlier cell already covers, or run off the grid, is
  clipped there (docling reports such overlaps: LRS-350 row 20 has a colspan-5 cell over three 1-col cells). The
  index keeps the converter's own span; only the HTML is clipped, so every cell stays visible at its position;
- two cells that start on the same slot share one element, the later one after a `<br>`, so neither text is lost.
"""

from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True)
class GridCell:
    row: int
    col: int
    rowspan: int
    colspan: int
    header: bool       # <th> rather than <td>
    text: str          # already escaped for raw HTML by the caller


def grid(cells: list[GridCell], rows: int = 0, cols: int = 0) -> tuple[int, int, list[tuple[int, int]]]:
    """The grid's size (at least `rows` × `cols`, grown to hold every cell's start) and each cell's span after
    clipping, in the order of `cells`. A cell that shares its start slot with an earlier one gets (0, 0)."""
    rows = max([rows, *(cell.row + 1 for cell in cells)])
    cols = max([cols, *(cell.col + 1 for cell in cells)])
    starts = {(cell.row, cell.col) for cell in cells}
    covered: set[tuple[int, int]] = set()
    spans = []
    for cell in sorted(range(len(cells)), key=lambda i: (cells[i].row, cells[i].col, i)):
        current = cells[cell]
        if (current.row, current.col) in covered:
            spans.append((cell, (0, 0)))
            continue

        def free(row: int, col: int) -> bool:
            return (row < rows and col < cols and (row, col) not in covered
                    and ((row, col) == (current.row, current.col) or (row, col) not in starts))

        colspan = 1
        while colspan < max(current.colspan, 1) and free(current.row, current.col + colspan):
            colspan += 1
        rowspan = 1
        while rowspan < max(current.rowspan, 1) and all(free(current.row + rowspan, current.col + offset)
                                                        for offset in range(colspan)):
            rowspan += 1
        for row in range(current.row, current.row + rowspan):
            for col in range(current.col, current.col + colspan):
                covered.add((row, col))
        spans.append((cell, (rowspan, colspan)))
    spans.sort()
    return rows, cols, [span for _cell, span in spans]


def html_table(cells: list[GridCell], rows: int = 0, cols: int = 0) -> tuple[str, list[tuple[int, int]]]:
    """The `<table>` markup and, per cell in the order of `cells`, the (start, end) code-point range of its text in
    that markup. The caller adds the offset at which it writes the markup."""
    rows, cols, spans = grid(cells, rows, cols)
    at: dict[tuple[int, int], list[int]] = {}
    covered: set[tuple[int, int]] = set()
    for index, (cell, (rowspan, colspan)) in enumerate(zip(cells, spans)):
        at.setdefault((cell.row, cell.col), []).append(index)
        for row in range(cell.row, cell.row + rowspan):
            for col in range(cell.col, cell.col + colspan):
                covered.add((row, col))

    parts: list[str] = []
    length = 0
    ranges: list[tuple[int, int] | None] = [None] * len(cells)

    def write(text: str) -> tuple[int, int]:
        nonlocal length
        start = length
        parts.append(text)
        length += len(text)
        return start, length

    write("<table>")
    for row in range(rows):
        write("\n<tr>")
        for col in range(cols):
            indices = at.get((row, col))
            if not indices:
                if (row, col) not in covered:
                    write("<td></td>")
                continue
            first = cells[indices[0]]
            rowspan, colspan = spans[indices[0]]
            tag = "th" if first.header else "td"
            attributes = (f' rowspan="{rowspan}"' if rowspan > 1 else "") + \
                         (f' colspan="{colspan}"' if colspan > 1 else "")
            write(f"<{tag}{attributes}>")
            for number, index in enumerate(indices):
                if number:
                    write("<br>")
                ranges[index] = write(cells[index].text)
            write(f"</{tag}>")
        write("</tr>")
    write("\n</table>")
    return "".join(parts), ranges
