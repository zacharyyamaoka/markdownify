"""Freeze the ruler: the truth scores perfect against itself, and each mutation of a perfect answer moves the
metric it should — and leaves alone the ones it should not touch.

WHY mutation tests: a gate that never goes red proves nothing (research 3-evaluation.md: "shift truth boxes by
one line → location KPI must go red; delete a row → TEDS and ledger must go red"). Each mutation edits a copy of
the truth bundle the way a real converter would get it wrong, keeping the bundle self-consistent (ranges shifted,
groundings travelling with their text), so only the intended error is present.
"""

from __future__ import annotations

import json
import logging
import pathlib
import shutil

import pdfplumber
import pytest

from harness import mdtext, render, score

ARMS = ("pdf", "png", "html")
CASES = sorted(path.name for path in render.CASES_DIR.iterdir() if (path / "source.md").exists())


# --- helpers ----------------------------------------------------------------------------------------------------

def copy_bundle(truth_dir: pathlib.Path, destination: pathlib.Path) -> pathlib.Path:
    destination.mkdir(parents=True)
    for name in ("document.md", "index.json"):
        shutil.copyfile(truth_dir / name, destination / name)
    return destination


def load(bundle: pathlib.Path) -> tuple[str, dict]:
    return (bundle / "document.md").read_text(encoding="utf-8"), json.loads((bundle / "index.json").read_text())


def save(bundle: pathlib.Path, document: str, index: dict) -> None:
    (bundle / "document.md").write_text(document, encoding="utf-8")
    (bundle / "index.json").write_text(json.dumps(index, ensure_ascii=False))


def remap(index: dict, mapping) -> None:
    """Move every word and block range through `mapping(offset) -> offset`."""
    for row in index["words"] + index["blocks"]:
        row["range"] = {"start": mapping(row["range"]["start"]), "end": mapping(row["range"]["end"])}


def replace_span(document: str, index: dict, start: int, end: int, text: str) -> str:
    """Replace document[start:end] with `text`; index words inside the span are removed, later ranges shift."""
    delta = len(text) - (end - start)
    index["words"] = [w for w in index["words"] if not (start <= w["range"]["start"] < end)]
    remap(index, lambda offset: offset + delta if offset >= end else offset)
    return document[:start] + text + document[end:]


def swap_spans(document: str, index: dict, first: tuple[int, int], second: tuple[int, int]) -> str:
    """Swap two non-overlapping spans; the index words inside each travel with their text (grounding unchanged)."""
    (a1, b1), (a2, b2) = sorted([first, second])
    length1, length2, middle = b1 - a1, b2 - a2, a2 - b1

    def mapping(offset: int) -> int:
        if a1 <= offset < b1:
            return a1 + length2 + middle + (offset - a1)
        if b1 <= offset < a2:
            return a1 + length2 + (offset - b1)
        if a2 <= offset < b2:
            return a1 + (offset - a2)
        return offset

    for row in index["words"]:
        start, end = row["range"]["start"], row["range"]["end"]
        row["range"] = {"start": mapping(start), "end": mapping(end - 1) + 1}
    index["words"].sort(key=lambda w: w["range"]["start"])
    return document[:a1] + document[a2:b2] + document[b1:a2] + document[a1:b1] + document[b2:]


def cell_span(document: str, row_label: str, column: int) -> tuple[int, int]:
    """The text span of a pipe-table cell: the `column`-th cell of the row starting with `row_label`."""
    line_start = document.index(f"| {row_label} |")
    line_end = document.index("\n", line_start)
    pipes = [i for i in range(line_start, line_end) if document[i] == "|"]
    return pipes[column] + 2, pipes[column + 1] - 1


def word_index_of(document: str, index: dict, text: str) -> int:
    return next(i for i, w in enumerate(index["words"])
                if document[w["range"]["start"]:w["range"]["end"]] == text)


def assert_text_perfect(scores):
    assert scores["text"]["ned"] == 0
    assert (scores["text"]["substitutions"], scores["text"]["deletions"], scores["text"]["insertions"]) == (0, 0, 0)


def assert_tables_perfect(scores):
    assert scores["tables"]["teds_mean"] == 1.0
    assert scores["tables"]["header_path_accuracy"] == 1.0


# --- truth against itself ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("case", CASES)
@pytest.mark.parametrize("arm", ARMS)
def test_truth_scores_perfect(rendered, case, arm):
    truth = rendered / case / arm
    scores = score.score(truth, truth)
    truth_extra = json.loads((truth / "truth.json").read_text())
    words = len(json.loads((truth / "index.json").read_text())["words"])

    assert scores["contract_problems"] == []
    assert_text_perfect(scores)
    if scores["tables"]["truth_tables"]:
        assert_tables_perfect(scores)
        assert scores["tables"]["teds_structure_mean"] == 1.0
    location = scores["location"]
    assert (location["correct"], location["misplaced"], location["unlinked"], location["ambiguous"]) == \
        (words, 0, 0, 0)
    assert location["median_iou"] == 1.0
    ledger = scores["ledger"]
    assert (ledger["lost"], ledger["unlinked"], ledger["linked"]) == (0, 0, words)
    expected_dropped, expected_flagged = {}, {}
    for word in truth_extra["source_words"]:
        if word["fate"] == "dropped":
            expected_dropped[word["reason"]] = expected_dropped.get(word["reason"], 0) + 1
        elif word["fate"] == "flagged":
            expected_flagged[word["kind"]] = expected_flagged.get(word["kind"], 0) + 1
    assert ledger["dropped"] == expected_dropped
    assert ledger["flagged"] == expected_flagged
    assert ledger["wrong_drops"] == ledger["intended_drops_kept"] == ledger["intended_drops_lost"] == 0
    for kind, row in scores["ladder"].items():
        assert row["represented"] == row["truth_represented"], kind


def test_cases_cover_the_brief(rendered):
    """The intended drops and the flagged plot are really in the truth, per arm."""
    drops = json.loads((rendered / "09-intended-drops" / "pdf" / "truth.json").read_text())["source_words"]
    assert {w["reason"] for w in drops if w["fate"] == "dropped"} == {"header", "footer"}
    drops = json.loads((rendered / "09-intended-drops" / "html" / "truth.json").read_text())["source_words"]
    assert {w["reason"] for w in drops if w["fate"] == "dropped"} == {"navigation"}
    plot = json.loads((rendered / "10-torque-speed-plot" / "pdf" / "index.json").read_text())["blocks"]
    assert [b["represented"] for b in plot if b["type"] == "plot"] == [False]
    merged = json.loads((rendered / "05-merged-header" / "pdf" / "index.json").read_text())["blocks"]
    assert any(b.get("colspan", 1) > 1 for b in merged) and any(b.get("rowspan", 1) > 1 for b in merged)


def test_tokenizer_matches_truth_words(rendered):
    for case in CASES:
        document, index = load(rendered / case / "pdf")
        assert [(w.start, w.end) for w in mdtext.words(document)] == \
            [(w["range"]["start"], w["range"]["end"]) for w in index["words"]], case


def test_self_check_goes_red_on_a_shifted_glyph(rendered, tmp_path):
    """The render's own gate: a DOM character 0.1 pt off its PDF glyph must fail the join."""
    source_pdf = rendered / "04-multi-model" / "pdf" / "source.pdf"
    logging.getLogger("pdfminer").setLevel(logging.ERROR)
    with pdfplumber.open(source_pdf) as pdf:
        chars = [c for c in pdf.pages[0].chars if not c["text"].isspace()]
    to_px = 1 / render.PX_TO_PT
    dom = [{"text": c["text"], "box_px": [c["x0"] * to_px, c["top"] * to_px, c["x1"] * to_px, c["bottom"] * to_px]}
           for c in chars]
    assert render.join_pdf_chars([dict(c) for c in dom], source_pdf)["passed"]
    dom[7]["box_px"][0] += 0.1 * to_px
    assert not render.join_pdf_chars(dom, source_pdf)["passed"]


# --- mutations --------------------------------------------------------------------------------------------------

@pytest.fixture
def multi_model(rendered, tmp_path, request):
    arm = getattr(request, "param", "pdf")
    truth = rendered / "04-multi-model" / arm
    return truth, copy_bundle(truth, tmp_path / "candidate")


@pytest.mark.parametrize("multi_model", ["pdf"], indirect=True)
def test_shift_one_box_misplaces_one_word(multi_model):
    truth, candidate = multi_model
    document, index = load(candidate)
    target = word_index_of(document, index, "column")
    box = index["words"][target]["grounding"]["box"]
    index["words"][target]["grounding"]["box"] = [box[0], box[1] + 0.03, box[2], box[3] + 0.03]   # ~ two lines
    save(candidate, document, index)
    scores = score.score(truth, candidate)
    assert scores["location"]["misplaced"] == 1
    assert scores["location"]["correct"] == len(index["words"]) - 1
    assert_text_perfect(scores)
    assert_tables_perfect(scores)
    assert scores["ledger"]["unlinked"] == 0
    assert scores["ledger"]["lost"] == 1        # its source word is no longer claimed by any link


@pytest.mark.parametrize("multi_model", ["pdf", "html"], indirect=True)
def test_drop_a_word(multi_model):
    truth, candidate = multi_model
    document, index = load(candidate)
    target = index["words"][word_index_of(document, index, "column")]["range"]
    document = replace_span(document, index, target["start"], target["end"] + 1, "")   # the word and its space
    save(candidate, document, index)
    scores = score.score(truth, candidate)
    assert (scores["text"]["substitutions"], scores["text"]["deletions"], scores["text"]["insertions"]) == (0, 1, 0)
    assert scores["text"]["ned"] > 0
    assert scores["location"]["unlinked"] == scores["location"]["unlinked_missing_text"] == 1
    assert scores["location"]["misplaced"] == 0
    assert scores["ledger"]["lost"] == 1
    assert scores["ledger"]["unlinked"] == 0
    assert_tables_perfect(scores)


@pytest.mark.parametrize("multi_model", ["pdf", "html"], indirect=True)
def test_add_an_invented_word(multi_model):
    truth, candidate = multi_model
    document, index = load(candidate)
    at = index["words"][word_index_of(document, index, "column")]["range"]["start"]
    document = replace_span(document, index, at, at, "turbocharged ")
    save(candidate, document, index)
    scores = score.score(truth, candidate)
    assert (scores["text"]["substitutions"], scores["text"]["deletions"], scores["text"]["insertions"]) == (0, 0, 1)
    assert scores["ledger"]["unlinked"] == 1
    assert scores["ledger"]["lost"] == 0
    assert scores["location"]["correct"] == len(index["words"])
    assert_tables_perfect(scores)


@pytest.mark.parametrize("multi_model", ["pdf", "html"], indirect=True)
def test_swap_two_table_cells(multi_model):
    """Two cells of one model column trade rows: the table is wrong, but every word still links truthfully."""
    truth, candidate = multi_model
    document, index = load(candidate)
    document = swap_spans(document, index, cell_span(document, "Rated voltage", 1), cell_span(document, "Weight", 1))
    save(candidate, document, index)
    scores = score.score(truth, candidate)
    assert scores["tables"]["teds_mean"] < 1.0
    assert scores["tables"]["header_path_accuracy"] < 1.0
    assert scores["text"]["ned"] > 0
    assert scores["ledger"]["lost"] == 0 and scores["ledger"]["unlinked"] == 0
    assert scores["location"]["unlinked"] == 0 and scores["location"]["ambiguous"] == 0


@pytest.mark.parametrize("multi_model", ["pdf", "html"], indirect=True)
def test_value_in_the_wrong_model_column(multi_model):
    """KM60-6's rated torque lands under KM70-10 and vice versa — the datasheet failure that matters most."""
    truth, candidate = multi_model
    document, index = load(candidate)
    document = swap_spans(document, index, cell_span(document, "Rated torque", 1),
                          cell_span(document, "Rated torque", 2))
    save(candidate, document, index)
    scores = score.score(truth, candidate)
    accuracy = scores["tables"]["header_path_accuracy"]
    assert accuracy == pytest.approx(1 - 2 / scores["tables"]["header_paths"])
    assert scores["tables"]["teds_mean"] < 1.0
    assert scores["ledger"]["lost"] == 0 and scores["ledger"]["unlinked"] == 0
    assert scores["location"]["misplaced"] >= 1      # the word now standing in the KM60-6 cell points at KM70-10's


def test_pdftotext_baseline_has_text_but_no_links(rendered, tmp_path):
    from harness.baselines import pdftotext

    truth = rendered / "03-simple-table" / "pdf"
    assert pdftotext.main([str(truth / "source.pdf"), str(tmp_path / "bundle")]) == 0
    scores = score.score(truth, tmp_path / "bundle")
    words = scores["text"]["truth_words"]
    assert scores["text"]["ned"] < 0.05
    assert scores["location"]["unlinked"] == words and scores["location"]["correct"] == 0
    assert scores["ledger"]["lost"] == words
    assert scores["tables"]["teds_mean"] == 0.0
