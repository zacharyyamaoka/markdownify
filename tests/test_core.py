"""The core without a model: the ledger's fates, the checker's invariants, the fetcher's refusals, the escaping.

The PDFs here are written with PyMuPDF (the harness extra), so the text layer is known exactly; nothing in this file
loads docling. `test_end_to_end_pdf` at the bottom does, once.
"""

import json
import pathlib
import shutil

import pytest

from mdify import align, bundle, check, ledger
from mdify.cache import Cache, sha256_bytes, write_json
from mdify.converters import SourceWord
from mdify.docling_markdown import escape_html_text, escape_text
from mdify.fetch import FetchRefused, Fetcher, media_type_of

pymupdf = pytest.importorskip("pymupdf")

LINES = ["Rated torque 20 Nm", "Peak torque 60 Nm"]


def write_pdf(path: pathlib.Path, lines=LINES) -> pathlib.Path:
    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    for number, line in enumerate(lines):
        page.insert_text((72, 100 + 30 * number), line, fontsize=12, fontname="helv")
    document.save(path)
    return path


def hand_bundle(directory: pathlib.Path, pdf_path: pathlib.Path) -> pathlib.Path:
    """A bundle written by hand: one paragraph per line, each word grounded on pdfplumber's own word box."""
    from mdify import pdf

    directory.mkdir(parents=True, exist_ok=True)
    data = pdf_path.read_bytes()
    sha256 = sha256_bytes(data)
    shutil.copyfile(pdf_path, directory / "source.pdf")
    write_json(directory / "source.json", {"sha256": sha256, "bytes": len(data), "media_type": "application/pdf",
                                           "retrievals": []})
    markdown = "\n\n".join(LINES) + "\n"
    layer = pdf.text_layer(directory / "source.pdf")
    source_words = [word for page in layer for word in page["words"]]
    words = []
    for (start, end), source_word in zip(align.word_ranges_of(markdown), source_words):
        words.append({"range": {"start": start, "end": end},
                      "grounding": {"page": 1, "box": [round(v, 5) for v in source_word["box"]]}, "score": 1.0})
    blocks = []
    offset = 0
    for number, line in enumerate(LINES):
        blocks.append({"id": f"paragraph-{number + 1}", "type": "paragraph",
                       "range": {"start": offset, "end": offset + len(line)}, "grounding": [], "represented": True})
        offset += len(line) + 2
    markdown_bytes = markdown.encode()
    index = {"schema": bundle.INDEX_SCHEMA, "tool": {"name": "mdify", "version": "test", "pipeline": "A"},
             "source": {"sha256": sha256, "media_type": "application/pdf"},
             "markdown_sha256": sha256_bytes(markdown_bytes), "range_units": "unicode_codepoints",
             "pages": [{"page": 1, "width": 595, "height": 842, "unit": "pt"}],
             "blocks": blocks, "words": words, "dropped": []}
    (directory / "document.md").write_bytes(markdown_bytes)
    write_json(directory / "index.json", index)
    from mdify.converters import Source
    source = Source(path=directory / "source.pdf", sha256=sha256, media_type="application/pdf")
    write_json(directory / "ledger.json", ledger.compute(index, markdown, pdf.inventory(source), "mdify.pdf.inventory"))
    return directory


def results_by_name(directory):
    return {result.name: result for result in check.check(directory)}


# --- ledger -----------------------------------------------------------------------------------------------------
def test_fates_by_geometry():
    index = {"words": [{"grounding": {"page": 1, "box": [0.1, 0.1, 0.2, 0.12]}}],
             "dropped": [{"grounding": {"page": 1, "box": [0, 0, 1, 0.05]}, "reason": "header"}],
             "blocks": [{"type": "figure", "represented": False, "grounding": [{"page": 1, "box": [0.5, 0.5, 0.9, 0.9]}]}]}
    inventory = [SourceWord("linked", {"page": 1, "box": [0.1, 0.1, 0.2, 0.12]}),
                 SourceWord("header", {"page": 1, "box": [0.4, 0.01, 0.5, 0.03]}),
                 SourceWord("axis", {"page": 1, "box": [0.6, 0.6, 0.65, 0.62]}),
                 SourceWord("lost", {"page": 1, "box": [0.1, 0.3, 0.2, 0.32]}),
                 SourceWord("other page", {"page": 2, "box": [0.1, 0.1, 0.2, 0.12]})]
    assert ledger.fates_of(index, inventory) == ["linked", "dropped:header", "flagged:figure", "lost", "lost"]


def test_a_word_covering_half_a_source_word_links_it():
    index = {"words": [{"grounding": {"page": 1, "box": [0.1, 0.1, 0.15, 0.12]}}], "dropped": [], "blocks": []}
    assert ledger.fates_of(index, [SourceWord("10A", {"page": 1, "box": [0.1, 0.1, 0.2, 0.12]})]) == ["linked"]


def test_kpis_coverage_and_traceability():
    index = {"pages": [{"page": 1}], "blocks": [], "words": [{"range": {"start": 0, "end": 1}, "grounding": None}]}
    the_ledger = {"source": {"available": True, "words": 10, "linked": 6, "dropped": 2, "flagged": 0, "lost": 2},
                  "markdown": {"words": 4, "linked": 3, "unlinked": 1, "inexact": 0}}
    kpis = ledger.kpis_of(index, the_ledger, {"seconds": 2.0, "cold": True, "model_load_seconds": 1.0})
    assert kpis["coverage"] == 0.75 and kpis["traceability"] == 0.75 and kpis["silently_lost"] == 2
    assert kpis["time"]["seconds_per_page"] == 2.0


# --- check ------------------------------------------------------------------------------------------------------
def test_check_passes_a_faithful_bundle(tmp_path):
    directory = hand_bundle(tmp_path / "bundle", write_pdf(tmp_path / "in.pdf"))
    results = results_by_name(directory)
    assert all(result.ok for result in results.values()), check.report(directory, list(results.values()))
    assert "8 exact" in results["faithful"].summary


def test_check_catches_a_word_pointing_at_the_wrong_box(tmp_path):
    directory = hand_bundle(tmp_path / "bundle", write_pdf(tmp_path / "in.pdf"))
    index = json.loads((directory / "index.json").read_text())
    index["words"][0]["grounding"], index["words"][1]["grounding"] = index["words"][1]["grounding"], index["words"][0]["grounding"]
    write_json(directory / "index.json", index)
    results = results_by_name(directory)
    assert not results["faithful"].ok and len(results["faithful"].failures) == 2


def test_check_catches_an_edited_markdown_and_an_overlap(tmp_path):
    directory = hand_bundle(tmp_path / "bundle", write_pdf(tmp_path / "in.pdf"))
    (directory / "document.md").write_text("Rated torque 21 Nm\n\nPeak torque 60 Nm\n")
    index = json.loads((directory / "index.json").read_text())
    index["words"][1]["range"]["start"] = 2
    write_json(directory / "index.json", index)
    results = results_by_name(directory)
    assert not results["pinned"].ok
    assert not results["well formed"].ok


def test_check_catches_untraceable_text(tmp_path):
    directory = hand_bundle(tmp_path / "bundle", write_pdf(tmp_path / "in.pdf"))
    index = json.loads((directory / "index.json").read_text())
    del index["words"][2]
    write_json(directory / "index.json", index)
    assert not results_by_name(directory)["traceable"].ok


def test_check_catches_a_ledger_that_hides_a_lost_word(tmp_path):
    directory = hand_bundle(tmp_path / "bundle", write_pdf(tmp_path / "in.pdf"))
    index = json.loads((directory / "index.json").read_text())
    index["words"][3]["grounding"] = None
    write_json(directory / "index.json", index)
    assert not results_by_name(directory)["accounted"].ok  # the ledger still says "linked"


def _table_index(markdown: str, cells: list[tuple[int, int, int, int]], cols: int) -> dict:
    """A one-table index over `markdown`: (row, col, rowspan, colspan) per cell, ranges left empty at the start."""
    children = [{"id": f"table_cell-{n + 1}", "type": "table_cell", "range": {"start": 0, "end": 0}, "row": row,
                 "col": col, "rowspan": rowspan, "colspan": colspan, "kind": "content", "grounding": []}
                for n, (row, col, rowspan, colspan) in enumerate(cells)]
    table = {"id": "table-1", "type": "table", "range": {"start": 0, "end": len(markdown)}, "grounding": [],
             "represented": True, "rows": 1 + max(row for row, *_ in cells), "cols": cols,
             "children": [child["id"] for child in children]}
    return {"words": [], "blocks": [table, *children], "pages": []}


def test_check_catches_a_table_row_shifted_left():
    # Mean Well LRS-350's header row as pipeline A wrote it: nothing covers col 1, so every model name sat one
    # column left of its values.
    markdown = ("<table>\n<tr><th>MODEL</th><th>A</th><th>B</th></tr>\n"
                "<tr><th rowspan=\"2\">OUTPUT</th><th>DC VOLTAGE</th><td>5V</td><td>12V</td></tr>\n"
                "<tr><th>RATED POWER</th><td>300W</td><td>348W</td></tr>\n</table>")
    index = _table_index(markdown, [(0, 0, 1, 1), (0, 2, 1, 1), (0, 3, 1, 1), (1, 0, 2, 1), (1, 1, 1, 1),
                                    (1, 2, 1, 1), (1, 3, 1, 1), (2, 1, 1, 1), (2, 2, 1, 1), (2, 3, 1, 1)], cols=4)
    result = check._well_formed(index, markdown)
    assert not result.ok
    assert result.failures == ["table-1 row 0 covers 3 of 4 columns; nothing covers col 3"]
    fixed = markdown.replace("<th>MODEL</th>", "<th>MODEL</th><td></td>")
    assert check._well_formed(index, fixed).ok


def test_check_catches_a_ragged_gfm_table_and_a_row_that_spills():
    gfm = "| a | b |\n|---|---|\n| 1 |"
    result = check._well_formed(_table_index(gfm, [(0, 0, 1, 1), (0, 1, 1, 1), (1, 0, 1, 1)], cols=2), gfm)
    assert result.failures == ["table-1 row 1 covers 1 of 2 columns; nothing covers col 1"]
    spill = "<table>\n<tr><td colspan=\"2\">a</td><td>b</td></tr>\n</table>"
    result = check._well_formed(_table_index(spill, [(0, 0, 1, 2), (0, 1, 1, 1)], cols=2), spill)
    assert result.failures[-1] == "table-1 row 0 covers 3 of 2 columns; spills into col 2"


# --- escaping ---------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text", ["5*3 = 15", "snake_case _x_", "a|b", "# not a heading", "1. not a list",
                                  "- not a bullet", "<b>not a tag</b>", "&amp; literal", "[x](y)", "back\\slash",
                                  "~~strike~~", "`tick`"])
def test_escaped_text_reads_back_as_the_same_words(text):
    words = [word.text for word in align.markdown_words(escape_text(text))]
    assert words == text.split()


@pytest.mark.parametrize("text", ["5*3", "_x_", "<b>", "&amp;", "a\\b", "~1~"])
def test_html_cell_text_reads_back_as_the_same_words(text):
    markdown = f"<table>\n<tr><td>{escape_html_text(text)}</td></tr>\n</table>"
    assert [word.text for word in align.markdown_words(markdown)] == text.split()


def test_table_pipes_escape_only_in_tables():
    assert escape_text("a|b", in_table=True) == "a\\|b"
    assert escape_text("a|b") == "a|b"


# --- fetch ------------------------------------------------------------------------------------------------------
def test_refused_hosts_are_never_requested(tmp_path):
    fetcher = Fetcher(Cache(tmp_path))
    for url in ("https://www.mcmaster.com/91251A540/", "https://octopart.com/x", "https://api.nexar.com/y"):
        with pytest.raises(FetchRefused):
            fetcher.fetch(url)
    assert not (tmp_path / "fetches.jsonl").exists()


def test_media_types_are_sniffed_from_the_bytes():
    assert media_type_of("application/octet-stream", b"%PDF-1.7 ...") == "application/pdf"
    assert media_type_of(None, b"\x89PNG\r\n\x1a\n....") == "image/png"
    assert media_type_of(None, b"\xff\xd8\xff\xe0....") == "image/jpeg"
    assert media_type_of("text/html; charset=utf-8", b"<!doctype html>") == "text/html"


def test_unknown_media_type_has_no_converter():
    with pytest.raises(bundle.NoConverter):
        bundle.converter_for("application/zip")


# --- end to end (loads docling once) ----------------------------------------------------------------------------
def test_end_to_end_pdf(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKDOWNIFY_CACHE", str(tmp_path / "cache"))
    lines = ["Actuator datasheet", "Rated torque 20 Nm at 24 V.", "Peak torque 60 Nm for 2 s.",
             "Efficient ﬁeld-oriented control."]
    source = write_pdf(tmp_path / "datasheet.pdf", lines)
    built = bundle.build(str(source), tmp_path / "bundle", cache=Cache(tmp_path / "cache"))
    kpis = built.kpis
    assert kpis["silently_lost"] == 0 and kpis["unlinked"] == 0
    assert kpis["coverage"] == 1.0 and kpis["traceability"] == 1.0
    markdown = (built.directory / "document.md").read_text()
    assert "Rated torque 20 Nm at 24 V." in markdown
    assert all(result.ok for result in check.check(built.directory))


def test_reader_is_passed_only_to_a_converter_that_has_it(tmp_path, monkeypatch):
    from mdify import converters

    calls = []

    class WithReader:
        __name__ = "fake.with_reader"
        MEDIA_TYPES = ("application/pdf",)
        PIPELINE = "A"

        @staticmethod
        def convert(source, *, reader=False):
            calls.append(reader)
            return converters.Conversion(markdown="", blocks=[], words=[], dropped=[], pages=[])

        @staticmethod
        def inventory(source):
            return None

    class WithoutReader(WithReader):
        @staticmethod
        def convert(source):
            calls.append("plain")
            return converters.Conversion(markdown="", blocks=[], words=[], dropped=[], pages=[])

    messages = []
    source = write_pdf(tmp_path / "in.pdf")
    for fake in (WithReader, WithoutReader):
        monkeypatch.setattr(bundle, "converter_for", lambda media_type, fake=fake: fake)
        bundle.build(str(source), tmp_path / fake.__name__, cache=Cache(tmp_path / "cache"), reader=True,
                     log=messages.append)
    assert calls == [True, "plain"]
    assert messages == ["mdify: --reader applies to web pages only; ignored for application/pdf"]


def test_check_refuses_a_source_only_directory_cleanly(tmp_path, capsys):
    from mdify import cli

    directory = tmp_path / "source-only"
    directory.mkdir()
    write_pdf(directory / "source.pdf")
    write_json(directory / "source.json", {"sha256": "0" * 64, "media_type": "application/pdf", "retrievals": []})
    assert cli.main(["check", str(directory)]) == 1
    error = capsys.readouterr().err
    assert "not a complete bundle: missing document.md, index.json, ledger.json (source only)" in error
    assert "Traceback" not in error
