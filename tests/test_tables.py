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


# --- reading order of a cell's words (JMC servo selection list, OCR path, 2026-10-29) ---------------------------
# The image prints each header on stacked lines; docling's OCR path joined the lines out of order ("voltage supply
# Power (V)"), and the extractor's label check then dropped correct values.
JMC_LINES = [("Power", (0.62, 0.106, 0.65, 0.121)), ("supply", (0.62, 0.119, 0.65, 0.137)),
             ("voltage", (0.62, 0.133, 0.65, 0.150)), ("(V)", (0.63, 0.146, 0.645, 0.164))]


def jmc_document(cell_text: str, paragraph_text: str):
    from docling_core.types.doc import (BoundingBox, CoordOrigin, DocItemLabel, DoclingDocument, ProvenanceItem,
                                        Size)

    document = DoclingDocument(name="jmc")
    document.add_page(page_no=1, size=Size(width=1000, height=1000))

    def box(x0, y0, x1, y1):
        return BoundingBox(l=x0 * 1000, t=y0 * 1000, r=x1 * 1000, b=y1 * 1000, coord_origin=CoordOrigin.TOPLEFT)

    cell = TableCell(text=cell_text, bbox=box(0.62, 0.105, 0.655, 0.165), start_row_offset_idx=0,
                     end_row_offset_idx=1, start_col_offset_idx=0, end_col_offset_idx=1, column_header=True)
    document.add_table(data=TableData(table_cells=[cell], num_rows=1, num_cols=1),
                       prov=ProvenanceItem(page_no=1, bbox=box(0.6, 0.1, 0.7, 0.2), charspan=(0, 0)))
    document.add_text(label=DocItemLabel.TEXT, text=paragraph_text, orig=paragraph_text,
                      prov=ProvenanceItem(page_no=1, bbox=box(0.1, 0.3, 0.5, 0.36), charspan=(0, 0)))
    return document


def jmc_units():
    from mdify.docling_markdown import SourceText

    units = [SourceText(text=text, raw=text, page=1, box=box) for text, box in JMC_LINES]
    # Two lines of a paragraph, the second line's words read first by the converter.
    units += [SourceText(text="Rated torque", raw="Rated torque", page=1, box=(0.1, 0.30, 0.4, 0.32)),
              SourceText(text="at 24 V.", raw="at 24 V.", page=1, box=(0.1, 0.33, 0.3, 0.35))]
    return units


def test_cell_and_block_text_is_in_visual_reading_order():
    from mdify.docling_markdown import serialize

    units = jmc_units()
    conversion = serialize(jmc_document("voltage supply Power (V)", "at 24 V. Rated torque"), units, pages=[
        {"page": 1, "width": 1000, "height": 1000, "unit": "px"}])
    markdown = conversion.markdown
    [cell] = [block for block in conversion.blocks if block["type"] == "table_cell"]
    assert markdown[cell["range"]["start"]:cell["range"]["end"]] == "Power supply voltage (V)"
    assert "Rated torque at 24 V." in markdown
    # Every word keeps the box of the source line it came from.
    for word in conversion.words:
        text = markdown[word["range"]["start"]:word["range"]["end"]]
        source = next(unit for unit in units if text in unit.text.split())
        assert word["grounding"]["box"][1] == pytest.approx(source.box[1], abs=1e-4), text
        assert word["score"] == 1.0


def test_reading_order_keeps_the_converters_text_when_it_is_not_a_reordering_of_the_source():
    from mdify.docling_markdown import serialize

    # "Supply" is not a source word: the text is not just the source's lines reordered, so it is left alone.
    conversion = serialize(jmc_document("voltage Supply Power (V)", "Rated torque at 24 V."), jmc_units(), pages=[
        {"page": 1, "width": 1000, "height": 1000, "unit": "px"}])
    assert "| voltage Supply Power (V) |" in conversion.markdown
    assert "Rated torque at 24 V." in conversion.markdown


def test_words_of_a_reordered_cell_keep_their_own_lines_box():
    # JMC's encoder cell: OCR returned the "2500C:" line before the "2500:" line printed above it. Aligned in OCR
    # order, each word was pinned to the other line (score 0.83).
    from docling_core.types.doc import BoundingBox, CoordOrigin, DoclingDocument, ProvenanceItem, Size

    from mdify.docling_markdown import SourceText, serialize

    lines = [("1024C: 1024 lines magnetic encoder;", 0.757), ("2500C: 2500 lines magnetic encoder", 0.789),
             ("2500: 2500 lines optical encoder;", 0.773)]                       # OCR order, not printed order
    units = [SourceText(text=text, raw=text, page=1, box=(0.18, top, 0.32, top + 0.016)) for text, top in lines]
    document = DoclingDocument(name="encoder")
    document.add_page(page_no=1, size=Size(width=1000, height=1000))
    cell = TableCell(text=" ".join(text for text, _top in lines), start_row_offset_idx=0, end_row_offset_idx=1,
                     start_col_offset_idx=0, end_col_offset_idx=1,
                     bbox=BoundingBox(l=170, t=750, r=330, b=810, coord_origin=CoordOrigin.TOPLEFT))
    document.add_table(data=TableData(table_cells=[cell], num_rows=1, num_cols=1),
                       prov=ProvenanceItem(page_no=1, bbox=BoundingBox(l=100, t=700, r=400, b=900,
                                                                       coord_origin=CoordOrigin.TOPLEFT),
                                           charspan=(0, 0)))
    conversion = serialize(document, units, pages=[{"page": 1, "width": 1000, "height": 1000, "unit": "px"}])
    assert ("| 1024C: 1024 lines magnetic encoder; 2500: 2500 lines optical encoder; "
            "2500C: 2500 lines magnetic encoder |") in conversion.markdown
    markdown = conversion.markdown
    for word in conversion.words:
        text = markdown[word["range"]["start"]:word["range"]["end"]]
        assert word["score"] == 1.0, text
        if text in ("2500:", "optical"):
            assert word["grounding"]["box"][1] == pytest.approx(0.773, abs=1e-4), text
        if text in ("2500C:",):
            assert word["grounding"]["box"][1] == pytest.approx(0.789, abs=1e-4), text
