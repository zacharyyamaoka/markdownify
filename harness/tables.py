"""Tables as grids, scored two ways: TEDS for structure + content, header paths for "which value belongs to which
model".

WHY pandoc reads the tables: a candidate's markdown can hold a table as a GFM pipe table or as a raw HTML `<table>`
(the contract allows both; merged headers need the second). Pandoc turns both into HTML `<table>`, so one parser
covers both and the ruler writes no table grammar of its own.

WHY header paths next to TEDS: TEDS (PubTabNet's tree edit distance) prices every cell the same, so a value bound
to the wrong model column costs as little as a typo. For a datasheet that binding is the failure that matters
(research 3-evaluation.md, Metrics §b), so each content cell is also scored as the triple
(row label, column header path, value), and a triple only counts if all three agree.
"""

from __future__ import annotations

import dataclasses
import functools
import subprocess

import lxml.html
from apted import APTED, Config
from rapidfuzz.distance import Levenshtein

from harness import mdtext


@dataclasses.dataclass
class Cell:
    text: str
    row: int
    col: int
    rowspan: int = 1
    colspan: int = 1
    header: bool = False      # a <th>, or any cell of a <thead> row


@dataclasses.dataclass
class Table:
    cells: list[Cell]

    @property
    def rows(self) -> int:
        return max((cell.row + cell.rowspan for cell in self.cells), default=0)

    @property
    def cols(self) -> int:
        return max((cell.col + cell.colspan for cell in self.cells), default=0)

    def header_rows(self) -> int:
        """Leading rows in which every cell is a header cell."""
        count = 0
        for row in range(self.rows):
            starting = [cell for cell in self.cells if cell.row == row]
            if starting and all(cell.header for cell in starting):
                count = row + 1
            else:
                break
        return count


def layout(rows: list[list[tuple[int, int]]]) -> list[list[tuple[int, int]]]:
    """The (row, col) of every cell given each row's (rowspan, colspan) list — the HTML table grid algorithm."""
    occupied: set[tuple[int, int]] = set()
    positions: list[list[tuple[int, int]]] = []
    for row_index, row in enumerate(rows):
        col = 0
        placed = []
        for rowspan, colspan in row:
            while (row_index, col) in occupied:
                col += 1
            placed.append((row_index, col))
            for dr in range(rowspan):
                for dc in range(colspan):
                    occupied.add((row_index + dr, col + dc))
            col += colspan
        positions.append(placed)
    return positions


def _span(element, name: str) -> int:
    try:
        return max(1, int(element.get(name, "1")))
    except ValueError:
        return 1


def table_from_element(table_element) -> Table:
    row_elements = [row for row in table_element.iter("tr")]
    cell_elements = [[cell for cell in row if isinstance(cell.tag, str) and cell.tag in ("td", "th")]
                     for row in row_elements]
    spans = [[(_span(cell, "rowspan"), _span(cell, "colspan")) for cell in row] for row in cell_elements]
    cells = []
    for row_element, row, placed in zip(row_elements, cell_elements, layout(spans)):
        in_head = row_element.getparent() is not None and row_element.getparent().tag == "thead"
        for element, (row_index, col_index) in zip(row, placed):
            text = " ".join(mdtext.normalize("".join(element.itertext())).split())
            cells.append(Cell(text, row_index, col_index, _span(element, "rowspan"), _span(element, "colspan"),
                              header=in_head or element.tag == "th"))
    return Table(cells)


@functools.lru_cache(maxsize=64)
def markdown_to_html(markdown: str) -> str:
    result = subprocess.run(["pandoc", "-f", "commonmark_x-smart", "-t", "html"], input=markdown,
                            capture_output=True, text=True, check=True)
    return result.stdout


def tables_from_markdown(markdown: str) -> list[Table]:
    html = markdown_to_html(markdown)
    if "<table" not in html:
        return []
    root = lxml.html.fromstring(f"<div>{html}</div>")
    return [table_from_element(element) for element in root.iter("table")]


# --- TEDS ---------------------------------------------------------------------------------------------------------

class _Node:
    def __init__(self, tag: str, colspan: int = 1, rowspan: int = 1, content: str = "", children=None):
        self.tag, self.colspan, self.rowspan, self.content = tag, colspan, rowspan, content
        self.children = children or []

    def size(self) -> int:
        return 1 + sum(child.size() for child in self.children)


class _TedsConfig(Config):
    def __init__(self, structure_only: bool):
        self.structure_only = structure_only

    def rename(self, node1, node2):
        if node1.tag != node2.tag or node1.colspan != node2.colspan or node1.rowspan != node2.rowspan:
            return 1.0
        if node1.tag == "td" and not self.structure_only:
            return Levenshtein.normalized_distance(node1.content, node2.content)
        return 0.0

    def children(self, node):
        return node.children


def _tree(table: Table) -> _Node:
    # PubTabNet's tree: table → tr → td (th counts as td: header-ness is scored by header paths, not here).
    rows = []
    for row in range(table.rows):
        cells = sorted((cell for cell in table.cells if cell.row == row), key=lambda cell: cell.col)
        rows.append(_Node("tr", children=[_Node("td", cell.colspan, cell.rowspan, cell.text) for cell in cells]))
    return _Node("table", children=rows)


def teds(truth: Table, candidate: Table, structure_only: bool = False) -> float:
    truth_tree, candidate_tree = _tree(truth), _tree(candidate)
    distance = APTED(truth_tree, candidate_tree, _TedsConfig(structure_only)).compute_edit_distance()
    return 1.0 - distance / max(truth_tree.size(), candidate_tree.size())


def match_tables(truth: list[Table], candidate: list[Table]) -> list[tuple[int, int | None, float]]:
    """Pair each truth table with the unused candidate table it scores best against (TEDS), greedily, best first."""
    scored = sorted(((teds(t, c), ti, ci) for ti, t in enumerate(truth) for ci, c in enumerate(candidate)),
                    reverse=True)
    used_truth, used_candidate, pairs = set(), set(), {}
    for score, truth_index, candidate_index in scored:
        if truth_index in used_truth or candidate_index in used_candidate:
            continue
        used_truth.add(truth_index)
        used_candidate.add(candidate_index)
        pairs[truth_index] = (candidate_index, score)
    return [(index, *pairs.get(index, (None, 0.0))) for index in range(len(truth))]


# --- header paths -------------------------------------------------------------------------------------------------

def header_paths(table: Table) -> list[tuple[str, str, str]]:
    """(row label, column header path, value) for every content cell.

    The row label is the text in column 0 of the cell's row; the column path joins the distinct header texts above
    the cell's column ("Torque / Rated"). Cells in the header rows or in column 0 are labels, not values.
    """
    grid: dict[tuple[int, int], Cell] = {}
    for cell in table.cells:
        for dr in range(cell.rowspan):
            for dc in range(cell.colspan):
                grid[(cell.row + dr, cell.col + dc)] = cell
    header_rows = table.header_rows()
    triples = []
    for cell in table.cells:
        if cell.row < header_rows or cell.col == 0:
            continue
        label_cell = grid.get((cell.row, 0))
        path: list[str] = []
        for row in range(header_rows):
            header = grid.get((row, cell.col))
            if header is not None and header.text and (not path or path[-1] != header.text):
                path.append(header.text)
        triples.append((label_cell.text if label_cell else "", " / ".join(path), cell.text))
    return triples
