"""Pipeline B (markdownify.vlm) without a model: the markdown it assembles from a reply, the word boxes it splits out of
line boxes, the scores (text layer or `null`), the CLI event parsers, and a whole bundle with the CLI call faked.
The real model runs are the harness's (scripts/pipeline_b_results/)."""

import json
import pathlib

import pytest

from markdownify import align, check, vlm, vlm_llm
from markdownify.cache import Cache

pymupdf = pytest.importorskip("pymupdf")

LINES = ["Rated torque 20 Nm", "Peak torque 60 Nm"]


def write_pdf(path: pathlib.Path, lines=LINES) -> pathlib.Path:
    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    for number, line in enumerate(lines):
        page.insert_text((72, 100 + 30 * number), line, fontsize=12, fontname="helv")
    document.save(path)
    return path


def text_of(markdown, word):
    return align.word_text(markdown, word["range"]["start"], word["range"]["end"])


# --- assembling a reply -----------------------------------------------------------------------------------------
REPLY = {"blocks": [
    {"type": "page_header", "level": 0, "box": [0, 0, 100, 10], "lines": [{"text": "Kestrel Rev 1", "box": [0, 0, 100, 10]}],
     "cells": []},
    {"type": "heading", "level": 1, "box": [10, 20, 90, 30], "lines": [{"text": "KM80 Specs", "box": [10, 20, 90, 30]}],
     "cells": []},
    {"type": "paragraph", "level": 0, "box": [10, 40, 90, 60],
     "lines": [{"text": "Torque 9 N·m", "box": [10, 40, 90, 50]}, {"text": "- at 48 V", "box": [10, 50, 50, 60]}],
     "cells": []},
    {"type": "list_item", "level": 0, "box": [10, 62, 90, 70], "lines": [{"text": "• Rated 24 V", "box": [10, 62, 90, 70]}],
     "cells": []},
    {"type": "list_item", "level": 0, "box": [10, 71, 90, 79], "lines": [{"text": "• Peak 52 V", "box": [10, 71, 90, 79]}],
     "cells": []},
    {"type": "table", "level": 0, "box": [10, 80, 90, 90], "lines": [], "cells": [
        {"row": 0, "col": 0, "rowspan": 1, "colspan": 1, "kind": "stubHead", "text": "Model", "box": [10, 80, 40, 85]},
        {"row": 0, "col": 1, "rowspan": 1, "colspan": 1, "kind": "columnHeader", "text": "Torque<sup>1</sup>",
         "box": [40, 80, 90, 85]},
        {"row": 1, "col": 0, "rowspan": 1, "colspan": 1, "kind": "rowHeader", "text": "KM80|9", "box": [10, 85, 40, 90]},
        {"row": 1, "col": 1, "rowspan": 1, "colspan": 1, "kind": "content", "text": "9 N·m", "box": [40, 85, 90, 90]}]},
    {"type": "plot", "level": 0, "box": [10, 91, 90, 99], "lines": [], "cells": []},
]}


@pytest.fixture
def assembled():
    return vlm.assemble([(1, REPLY, 100, 100)], "pixel")


def test_markdown_from_a_reply(assembled):
    assert assembled.markdown == ("# KM80 Specs\n\nTorque 9 N·m - at 48 V\n\n- Rated 24 V\n- Peak 52 V\n\n"
                                  "| Model | Torque<sup>1</sup> |\n|---|---|\n| KM80\\|9 | 9 N·m |\n")


def test_a_continuation_line_never_becomes_a_list(assembled):
    assert "\n- at 48 V" not in assembled.markdown and "Torque 9 N·m - at 48 V" in assembled.markdown


def test_every_word_is_grounded_on_its_line_slice(assembled):
    markdown = assembled.markdown
    words = {text_of(markdown, word): word for word in assembled.words}
    assert all(word["grounding"] for word in assembled.words)
    # "KM80 Specs": 10 printed characters over x 0.10–0.90 → "KM80" is the first 4.
    assert words["KM80"]["grounding"]["box"] == [0.1, 0.2, 0.42, 0.3]
    assert words["Specs"]["grounding"]["box"] == [0.5, 0.2, 0.9, 0.3]
    # The second line of the paragraph keeps its own box.
    assert words["48"]["grounding"]["box"][1] == 0.5


def test_a_stripped_bullet_keeps_its_place_and_is_dropped(assembled):
    markdown = assembled.markdown
    rated = next(word for word in assembled.words if text_of(markdown, word) == "Rated")
    # "• Rated 24 V": 12 printed characters, "• " is the first 2, so "Rated" starts 2/12 of the way along.
    assert rated["grounding"]["box"][0] == pytest.approx(0.1 + 0.8 * 2 / 12, abs=1e-4)
    bullets = [region for region in assembled.dropped if region["reason"] == "boilerplate"]
    assert len(bullets) == 2 and bullets[0]["grounding"]["box"][2] == pytest.approx(0.1 + 0.8 * 2 / 12, abs=1e-4)


def test_headers_are_dropped_and_plots_flagged(assembled):
    assert {"grounding": {"page": 1, "box": [0.0, 0.0, 1.0, 0.1]}, "reason": "header"} in assembled.dropped
    assert "Kestrel" not in assembled.markdown
    plot = next(block for block in assembled.blocks if block["type"] == "plot")
    assert plot["represented"] is False and plot["range"]["start"] == plot["range"]["end"]


def test_table_cells_have_ranges_kinds_and_boxes(assembled):
    cells = [block for block in assembled.blocks if block["type"] == "table_cell"]
    assert [(cell["row"], cell["col"], cell["kind"]) for cell in cells] == [
        (0, 0, "stubHead"), (0, 1, "columnHeader"), (1, 0, "rowHeader"), (1, 1, "content")]
    texts = [align.word_text(assembled.markdown, cell["range"]["start"], cell["range"]["end"]) for cell in cells]
    assert texts == ["Model", "Torque1", "KM80|9", "9N·m"]
    table = next(block for block in assembled.blocks if block["type"] == "table")
    assert table["children"] == [cell["id"] for cell in cells]


def test_a_merged_cell_makes_an_html_table():
    reply = {"blocks": [{"type": "table", "level": 0, "box": [0, 0, 10, 10], "lines": [], "cells": [
        {"row": 0, "col": 0, "rowspan": 1, "colspan": 2, "kind": "columnHeader", "text": "Torque", "box": [0, 0, 10, 5]},
        {"row": 1, "col": 0, "rowspan": 1, "colspan": 1, "kind": "content", "text": "3 & 4", "box": [0, 5, 5, 10]},
        {"row": 1, "col": 1, "rowspan": 1, "colspan": 1, "kind": "content", "text": "9", "box": [5, 5, 10, 10]}]}]}
    conversion = vlm.assemble([(1, reply, 10, 10)], "pixel")
    assert conversion.markdown == ('<table>\n<tr><th colspan="2">Torque</th></tr>\n<tr><td>3 &amp; 4</td>'
                                   '<td>9</td></tr>\n</table>\n')
    assert [text_of(conversion.markdown, word) for word in conversion.words] == ["Torque", "3", "&", "4", "9"]


def test_unit_and_pixel_boxes_normalise_the_same():
    assert vlm.normalised_box([50, 20, 100, 40], "pixel", 200, 100) == [0.25, 0.2, 0.5, 0.4]
    assert vlm.normalised_box([0.25, 0.2, 0.5, 0.4], "unit", 200, 100) == [0.25, 0.2, 0.5, 0.4]
    assert vlm.normalised_box([100, 40, 50, 20], "pixel", 200, 100) == [0.25, 0.2, 0.5, 0.4]
    assert vlm.normalised_box([1, 2, 3], "pixel", 200, 100) is None


def test_schema_is_strict_for_codex():
    def walk(node):
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert sorted(node["required"]) == sorted(node["properties"])
            for child in node["properties"].values():
                walk(child)
        elif node.get("type") == "array":
            walk(node["items"])
    walk(vlm.output_schema())


# --- the CLI event logs -----------------------------------------------------------------------------------------
def test_claude_and_codex_event_parsers(tmp_path):
    claude = tmp_path / "claude.jsonl"
    claude.write_text("\n".join(json.dumps(event) for event in [
        {"type": "system", "subtype": "init", "model": "claude-opus-5-5"},
        {"type": "assistant", "message": {"model": "claude-opus-5-5", "content": [{"type": "text", "text": "…"}]}},
        {"type": "result", "is_error": False, "structured_output": {"blocks": []}}]) + "\n")
    assert vlm_llm.parse_claude_events(claude) == ({"blocks": []}, "claude-opus-5-5", None)
    codex = tmp_path / "codex.jsonl"
    codex.write_text("\n".join(json.dumps(event) for event in [
        {"type": "thread.started", "thread_id": "t1"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": '{"blocks": []}'}}]) + "\n")
    assert vlm_llm.parse_codex_events(codex) == ({"blocks": []}, "t1", None)


# --- a whole bundle, the CLI faked ------------------------------------------------------------------------------
def fake_model(lines_of_page, shift=0.0):
    """A stand-in for vlm_llm.ask that answers with the given lines (text, pixel box) as paragraphs."""
    calls = []

    def ask(backend, image, system_prompt, prompt, schema, run_dir, model=None):
        calls.append({"backend": backend, "prompt": prompt, "system": system_prompt})
        run_dir.mkdir(parents=True, exist_ok=True)
        blocks = [{"type": "paragraph", "level": 0, "box": [x0 + shift, y0, x1 + shift, y1],
                   "lines": [{"text": text, "box": [x0 + shift, y0, x1 + shift, y1]}], "cells": []}
                  for text, (x0, y0, x1, y1) in lines_of_page]
        return vlm_llm.Reply(backend=backend, model="fake-model", output={"blocks": blocks}, error=None,
                             run_dir=run_dir, cli_version="fake", seconds=0.0)
    return ask, calls


def pdf_lines_in_pixels(path):
    """Each line of the test PDF with its exact pixel box at 192 DPI, from the text layer (a perfect model)."""
    import pdfplumber

    scale = 192 / 72
    with pdfplumber.open(path) as document:
        page = document.pages[0]
        lines = []
        for line in page.extract_text_lines():
            lines.append((line["text"], (line["x0"] * scale, line["top"] * scale, line["x1"] * scale,
                                         line["bottom"] * scale)))
    return lines


def test_bundle_from_a_pdf_is_scored_against_the_text_layer(tmp_path, monkeypatch):
    source = write_pdf(tmp_path / "in.pdf")
    ask, calls = fake_model(pdf_lines_in_pixels(source))
    monkeypatch.setattr(vlm_llm, "ask", ask)
    built = vlm.build(str(source), tmp_path / "bundle", cache=Cache(tmp_path / "cache"))
    index = json.loads((built.directory / "index.json").read_text())
    assert index["tool"]["pipeline"] == "B" and index["tool"]["models"] == ["fake-model"]
    # A4 at 192 DPI: 595 × 842 pt → 1587 × 2246 px (pdfium rounds up), the size the model is told.
    assert "1587 × 2246 px" in calls[0]["system"]
    assert built.kpis["silently_lost"] == 0 and built.kpis["unlinked"] == 0 and built.kpis["coverage"] == 1.0
    # Even with exact line boxes, a slice by character count misses a proportional font's narrow and wide glyphs:
    # "20" and "Nm" take in a neighbour's characters. Scored below 1.0 and listed — the split's known cost.
    scores = [word["score"] for word in index["words"]]
    assert all(isinstance(score, float) for score in scores) and min(scores) >= 0.5 and scores.count(1.0) >= 4
    assert built.kpis["inexact_words"] == len(scores) - scores.count(1.0)
    assert (built.directory / "vlm" / "page-1.png").is_file() and (built.directory / "vlm" / "run.json").is_file()
    results = {result.name: result for result in check.check(built.directory)}
    assert results["traceable"].ok and results["well formed"].ok and results["pinned"].ok


def test_a_misplaced_box_scores_below_one(tmp_path, monkeypatch):
    source = write_pdf(tmp_path / "in.pdf")
    ask, _ = fake_model(pdf_lines_in_pixels(source), shift=400)
    monkeypatch.setattr(vlm_llm, "ask", ask)
    built = vlm.build(str(source), tmp_path / "bundle", cache=Cache(tmp_path / "cache"))
    index = json.loads((built.directory / "index.json").read_text())
    assert all(word["score"] == 0.0 for word in index["words"])
    assert built.kpis["silently_lost"] == len(LINES[0].split()) + len(LINES[1].split())


def test_bundle_from_an_image_marks_every_word_unverified(tmp_path, monkeypatch):
    from PIL import Image

    image = tmp_path / "in.png"
    Image.new("RGB", (400, 200), "white").save(image)
    ask, _ = fake_model([("Rated torque 20 Nm", (10, 10, 200, 30))])
    monkeypatch.setattr(vlm_llm, "ask", ask)
    built = vlm.build(str(image), tmp_path / "bundle", cache=Cache(tmp_path / "cache"))
    index = json.loads((built.directory / "index.json").read_text())
    assert [word["score"] for word in index["words"]] == [None] * 4
    assert all(word["note"] == "unverified" for word in index["words"])
    the_ledger = json.loads((built.directory / "ledger.json").read_text())
    assert [entry["score"] for entry in the_ledger["markdown"]["inexact_words"]] == [None] * 4
    assert built.kpis["silently_lost"] is None


def test_a_failed_call_is_retried_once_then_fails_loudly(tmp_path, monkeypatch):
    source = write_pdf(tmp_path / "in.pdf")
    attempts = []

    def ask(backend, image, system_prompt, prompt, schema, run_dir, model=None):
        attempts.append(run_dir.name)
        run_dir.mkdir(parents=True, exist_ok=True)
        return vlm_llm.Reply(backend=backend, model=None, output=None, error="no structured reply", run_dir=run_dir,
                             cli_version=None, seconds=0.0)
    monkeypatch.setattr(vlm_llm, "ask", ask)
    with pytest.raises(RuntimeError, match="no structured reply"):
        vlm.build(str(source), tmp_path / "bundle", cache=Cache(tmp_path / "cache"))
    assert attempts == ["call-1", "call-2"]
    run = json.loads((tmp_path / "bundle" / "vlm" / "run.json").read_text())
    assert len(run["calls"]) == 2


# --- pipeline AB: --place words ---------------------------------------------------------------------------------
def test_place_words_puts_each_word_on_the_text_layer(tmp_path, monkeypatch):
    """The same exact line boxes that the character-count split scores 0.5 on "20" and "Nm" (above) place every
    word exactly once the text layer's own words do it."""
    source = write_pdf(tmp_path / "in.pdf")
    ask, _ = fake_model(pdf_lines_in_pixels(source))
    monkeypatch.setattr(vlm_llm, "ask", ask)
    built = vlm.build(str(source), tmp_path / "bundle", place="words", cache=Cache(tmp_path / "cache"))
    index = json.loads((built.directory / "index.json").read_text())
    assert index["tool"]["pipeline"] == "AB" and index["tool"]["place"] == "words"
    assert [word["score"] for word in index["words"]] == [1.0] * 8
    assert not any("placed" in word or "note" in word for word in index["words"])
    assert built.kpis["inexact_words"] == 0 and built.kpis["silently_lost"] == 0
    run = json.loads((built.directory / "vlm" / "run.json").read_text())
    assert run["placement"] == {"source": "text_layer", "source_words": 8, "words": 8, "placed": 8}
    assert all(result.ok for result in check.check(built.directory))


def test_place_words_falls_back_to_the_line_slice_when_nothing_matches(tmp_path, monkeypatch):
    source = write_pdf(tmp_path / "in.pdf")
    lines = pdf_lines_in_pixels(source)
    ask, _ = fake_model([("Nominal speed 300 rpm", lines[0][1])] + lines[1:])
    monkeypatch.setattr(vlm_llm, "ask", ask)
    built = vlm.build(str(source), tmp_path / "bundle", place="words", cache=Cache(tmp_path / "cache"))
    index = json.loads((built.directory / "index.json").read_text())
    markdown = (built.directory / "document.md").read_text()
    by_text = {text_of(markdown, word): word for word in index["words"]}
    # "speed" and "rpm" have no source word in their line: still on their slice, scored by the text layer under it.
    assert by_text["rpm"]["grounding"] is not None and by_text["rpm"]["score"] < 1.0
    assert by_text["Peak"]["score"] == 1.0


def test_place_words_on_an_image_uses_ocr_word_boxes(tmp_path, monkeypatch):
    from PIL import Image, ImageDraw, ImageFont

    image = tmp_path / "in.png"
    picture = Image.new("RGB", (900, 200), "white")
    font = ImageFont.load_default(size=40)
    ImageDraw.Draw(picture).text((40, 60), "Rated torque 20 Nm", fill="black", font=font)
    picture.save(image)
    # The model's line box is right, but it says one word the page does not ("40" for "20").
    ask, _ = fake_model([("Rated torque 40 Nm", (35, 55, 420, 110))])
    monkeypatch.setattr(vlm_llm, "ask", ask)
    built = vlm.build(str(image), tmp_path / "bundle", place="words", cache=Cache(tmp_path / "cache"))
    index = json.loads((built.directory / "index.json").read_text())
    markdown = (built.directory / "document.md").read_text()
    by_text = {text_of(markdown, word): word for word in index["words"]}
    assert by_text["Rated"]["note"] == "ocr" and by_text["Rated"]["score"] == 1.0
    assert by_text["40"]["score"] is None or by_text["40"]["score"] < 1.0
    run = json.loads((built.directory / "vlm" / "run.json").read_text())
    assert run["placement"]["source"] == "ocr" and run["placement"]["placed"] >= 3


def test_codex_gets_absolute_paths_from_a_relative_run_dir(tmp_path, monkeypatch):
    """codex runs in `-C <run_dir>/cli-cwd`: a relative image or schema path broke every codex call (2026-10-29)."""
    seen = {}

    def fake_run(cmd, stdin, run_dir, cwd):
        seen["cmd"] = cmd
        (run_dir / "events.jsonl").write_text("")
        (run_dir / "stderr.txt").write_text("")
        return 0, 0.0
    monkeypatch.setattr(vlm_llm, "_run", fake_run)
    monkeypatch.setattr(vlm_llm, "_version", lambda binary: None)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "page.png").write_bytes(b"")
    vlm_llm.ask("codex", pathlib.Path("page.png"), "system", "prompt", {}, pathlib.Path("run"))
    cmd = seen["cmd"]
    for flag in ("-i", "--output-schema", "-o", "-C"):
        assert pathlib.Path(cmd[cmd.index(flag) + 1]).is_absolute(), flag


def test_a_ragged_reply_table_is_written_on_a_rectangular_grid(tmp_path, monkeypatch):
    """A model's cells with a hole at (0, 1) and two cells on (2, 0): written row by row, row 0's "Peak" slid one
    column left (under "Rated"); `markdownify.tables.html_table` keeps every cell at its own column, and the bundle passes
    `markdownify check` invariant 4 (which refuses ragged tables)."""
    from PIL import Image

    image = tmp_path / "in.png"
    Image.new("RGB", (400, 300), "white").save(image)
    cells = [(0, 0, 1, 1, "stubHead", "Model"), (0, 2, 1, 1, "columnHeader", "Peak"),
             (1, 0, 1, 1, "rowHeader", "KM60"), (1, 1, 1, 1, "content", "3"), (1, 2, 1, 1, "content", "9"),
             (2, 0, 1, 2, "rowHeader", "KM80"), (2, 0, 1, 1, "content", "x"), (2, 2, 1, 1, "content", "18")]
    reply = {"blocks": [{"type": "table", "level": 0, "box": [0, 0, 300, 90], "lines": [], "cells": [
        {"row": row, "col": col, "rowspan": rowspan, "colspan": colspan, "kind": kind, "text": text,
         "box": [col * 100, row * 30, col * 100 + 90, row * 30 + 25]}
        for row, col, rowspan, colspan, kind, text in cells]}]}

    def ask(backend, image, system_prompt, prompt, schema, run_dir, model=None):
        run_dir.mkdir(parents=True, exist_ok=True)
        return vlm_llm.Reply(backend, "fake-model", reply, None, run_dir, "fake", 0.0)
    monkeypatch.setattr(vlm_llm, "ask", ask)
    built = vlm.build(str(image), tmp_path / "bundle", cache=Cache(tmp_path / "cache"))
    markdown = (built.directory / "document.md").read_text()
    assert markdown == ('<table>\n<tr><th>Model</th><td></td><th>Peak</th></tr>\n'
                        '<tr><td>KM60</td><td>3</td><td>9</td></tr>\n'
                        '<tr><td colspan="2">KM80<br>x</td><td>18</td></tr>\n</table>\n')
    index = json.loads((built.directory / "index.json").read_text())
    cell_texts = [align.word_text(markdown, block["range"]["start"], block["range"]["end"])
                  for block in index["blocks"] if block["type"] == "table_cell"]
    assert cell_texts == ["Model", "Peak", "KM60", "3", "9", "KM80", "x", "18"]
    results = {result.name: result for result in check.check(built.directory)}
    assert results["well formed"].ok, results["well formed"].failures
