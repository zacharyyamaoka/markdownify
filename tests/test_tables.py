"""The table grid (mdify.tables) and the checker's "tables are rectangular", without a model.

The shapes are Mean Well LRS-350's spec table as docling reads it (bundle ac3e7b8d…, 2026-10-29): "MODEL" in col 0
with nothing at col 1, the OUTPUT group label down col 0 with rowspan 11, the row labels in col 1, eight model
columns after them; and row 20, where docling reports a colspan-5 cell over three 1-col cells.
"""

import pytest
from docling_core.types.doc import TableCell, TableData, TableItem

from mdify import check
from mdify.docling_markdown import _Serializer
from mdify.tables import GridCell, html_table

MODELS = ["LRS-350-3.3", "LRS-350-4.2", "LRS-350-5", "LRS-350-12", "LRS-350-15", "LRS-350-24", "LRS-350-36",
          "LRS-350-48"]
POWER = ["198W", "252W", "300W", "348W", "348W", "350.4W", "349.2W", "350.4W"]
VOLTAGE = ["3.3V", "4.2V", "5V", "12V", "15V", "24V", "36V", "48V"]


def lrs350_cells(header_col: int = 0) -> list[tuple]:
    """(row, col, rowspan, colspan, header, text): the header row's label at `header_col` (0 as docling read the
    real sheet, leaving col 1 empty; 1 for the mirror shape, leaving col 0 empty), the body from col 0."""
    cells = [(0, header_col, 1, 1, True, "MODEL")]
    cells += [(0, 2 + n, 1, 1, True, model) for n, model in enumerate(MODELS)]
    cells += [(1, 0, 3, 1, True, "OUTPUT"), (1, 1, 1, 1, True, "DC VOLTAGE")]
    cells += [(1, 2 + n, 1, 1, False, value) for n, value in enumerate(VOLTAGE)]
    cells += [(2, 1, 1, 1, True, "RATED POWER")]
    cells += [(2, 2 + n, 1, 1, False, value) for n, value in enumerate(POWER)]
    cells += [(3, 1, 1, 1, True, "OVER VOLTAGE"), (3, 2, 1, 5, False, "Shut down"), (3, 3, 1, 1, False, "o/p"),
              (3, 4, 1, 1, False, "re-power"), (3, 7, 1, 1, False, "28.8V")]
    return cells


def column_of(table: check.MarkdownTable, text: str) -> int:
    found = [col for row in table.rows for col, _rowspan, _colspan, cell_text in row if cell_text == text]
    assert len(found) == 1, (text, found)
    return found[0]


def row_texts(table: check.MarkdownTable, row: int) -> dict[int, str]:
    return {col: text for col, _rowspan, _colspan, text in table.rows[row]}


def docling_table(cells: list[tuple], rows: int, cols: int) -> TableItem:
    table_cells = [TableCell(text=text, start_row_offset_idx=row, end_row_offset_idx=row + rowspan,
                             start_col_offset_idx=col, end_col_offset_idx=col + colspan, row_span=rowspan,
                             col_span=colspan, column_header=header and row == 0, row_header=header and row > 0)
                   for row, col, rowspan, colspan, header, text in cells]
    return TableItem(self_ref="#/tables/0", data=TableData(table_cells=table_cells, num_rows=rows, num_cols=cols))


# --- the converter's own table writer (pipeline A) --------------------------------------------------------------
@pytest.mark.parametrize("header_col", [0, 1])
def test_pipeline_a_writes_the_lrs350_corner_on_a_rectangular_grid(header_col):
    serializer = _Serializer(document=None, fold=None)
    serializer.table(docling_table(lrs350_cells(header_col), rows=4, cols=10))
    markdown = serializer.writer.text()
    [table] = check.markdown_tables(markdown)
    assert all(covered == set(range(10)) for covered in table.covered), table.covered
    # The bug: "350.4W" (index col 7) read as LRS-350-36's rating because the header row sat one column left.
    header, power = row_texts(table, 0), row_texts(table, 2)
    for number, model in enumerate(MODELS):
        assert header[2 + number] == model and power[2 + number] == POWER[number]
    assert column_of(table, "MODEL") == header_col


def test_pipeline_a_keeps_index_cells_and_ranges_exactly():
    cells = lrs350_cells()
    serializer = _Serializer(document=None, fold=None)
    serializer.table(docling_table(cells, rows=4, cols=10))
    markdown = serializer.writer.text()
    indexed = [block for block in serializer.blocks if block["type"] == "table_cell"]
    expected = sorted(cells, key=lambda cell: (cell[0], cell[1]))
    assert [(b["row"], b["col"], b["rowspan"], b["colspan"]) for b in indexed] == [c[:4] for c in expected]
    assert [markdown[b["range"]["start"]:b["range"]["end"]] for b in indexed] == [c[5] for c in expected]
    assert indexed[[c[5] for c in expected].index("350.4W")]["col"] == 7


# --- mdify.tables -----------------------------------------------------------------------------------------------
def test_html_table_fills_the_uncovered_slot_with_an_empty_cell():
    markup, _ranges = html_table([GridCell(*cell) for cell in lrs350_cells()], rows=4, cols=10)
    assert markup.splitlines()[1].startswith("<tr><th>MODEL</th><td></td><th>LRS-350-3.3</th>")


def test_html_table_clips_a_span_over_another_cells_start():
    cells = [GridCell(*cell) for cell in lrs350_cells()]
    markup, ranges = html_table(cells, rows=4, cols=10)
    [table] = check.markdown_tables(markup)
    assert table.covered[3] == set(range(10))
    # "Shut down" keeps its own column and stops before "o/p"; col 5–6, which nothing covers, are filled.
    assert ("Shut down" in markup and '<td colspan="5">' not in markup
            and column_of(table, "o/p") == 3 and column_of(table, "28.8V") == 7)
    assert [markup[start:end] for start, end in ranges] == [cell.text for cell in cells]


def test_html_table_keeps_both_cells_that_start_on_one_slot():
    cells = [GridCell(0, 0, 1, 1, False, "a"), GridCell(0, 0, 1, 1, False, "b"), GridCell(0, 1, 1, 1, False, "c")]
    markup, ranges = html_table(cells)
    assert markup == "<table>\n<tr><td>a<br>b</td><td>c</td></tr>\n</table>"
    assert [markup[start:end] for start, end in ranges] == ["a", "b", "c"]


def test_html_table_grows_the_grid_to_hold_every_cell_and_clips_spans_at_its_edge():
    cells = [GridCell(0, 0, 3, 4, True, "wide"), GridCell(1, 2, 1, 1, False, "x")]
    markup, _ranges = html_table(cells, rows=2, cols=2)
    [table] = check.markdown_tables(markup)
    assert table.covered == [{0, 1, 2}, {0, 1, 2}]
    assert column_of(table, "x") == 2


# --- the checker's parser ---------------------------------------------------------------------------------------
def test_markdown_tables_reads_html_and_gfm_and_skips_code():
    markdown = ("| a | b \\| c | d |\n|---|---|---|\n| 1 | 2 |\n\n"
                "```\n<table><tr><td>not a table</td></tr></table>\n```\n\n"
                "\\<table> is text\n\n"
                "<table>\n<tr><th rowspan=\"2\">g</th><th>x</th></tr>\n<tr><td>y</td><td>z</td></tr>\n</table>\n")
    gfm, html = check.markdown_tables(markdown)
    assert gfm.kind == "gfm" and [len(covered) for covered in gfm.covered] == [3, 3, 2]
    assert html.kind == "html" and html.covered == [{0, 1}, {0, 1, 2}]
    assert column_of(html, "y") == 1
