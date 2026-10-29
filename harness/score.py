"""Score a candidate bundle against a case's truth bundle: what the pipeline said, where it said it came from,
and what it left out. "IK" checked against "FK" (Zach, 2026-10-29).

    uv run python harness/score.py harness/out/04-multi-model/pdf <candidate bundle dir>

Writes <candidate>/scores.json and prints a table. Five families, each kept apart (never pooled across arms):

- text: normalised edit distance over the content words (rapidfuzz, after NFKC and whitespace normalisation) plus
  the jiwer split into substitutions / deletions / insertions. WHY the split: deletions are omissions and
  insertions are hallucinations; one averaged number hides which (research 3-evaluation.md, Metrics §a).
- tables: TEDS and structure-only TEDS per truth table (PubTabNet's metric, via apted), and header-path accuracy:
  the share of (row label, column header path, value) triples the candidate reproduces. A value under the wrong
  model column fails its triple even when TEDS barely moves.
- location: per truth word, the candidate word aligned to it is `correct` when its grounding passes ParseBench's
  rule — IoA(truth, candidate) ≥ 0.5 and IoA(candidate, truth) ≥ 0.2, IoA = intersection over the FIRST box's
  area — `misplaced` when it fails, `unlinked` when it has none, `ambiguous` when it points at boxes far apart.
  WHY IoA and not IoU: a DOM box and a text-layer box of the same glyph differ in height (research: a perfect
  answer scores IoU 0.78), so median IoU is reported but never gated. For the html arm the same rule runs on
  `source_range` overlap (1-D).
- ledger: the coverage ledger re-derived here from the truth's own source-word inventory — never from the
  candidate's ledger.json, so a pipeline cannot grade itself (CONTRACT §5, guard 1 in the research).
- ladder: per content kind (prose, simple table, complex table, plot, diagram), how many truth blocks the
  candidate represents.

The ruler is frozen: tests/harness pins that truth scores perfect against itself and that each mutation moves
the metric it should.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import statistics
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import jiwer
from rapidfuzz.distance import Levenshtein

from harness import mdtext, tables

LOCALISED_TRUTH_SHARE = 0.5         # ParseBench: IoA(truth, candidate) ≥ 0.5 ...
LOCALISED_CANDIDATE_SHARE = 0.2     # ... and IoA(candidate, truth) ≥ 0.2
PROSE_REPRESENTED_SHARE = 0.9       # a prose block counts as represented when 90 % of its words survive
TABLE_REPRESENTED_TEDS = 0.9
HTML_AMBIGUITY_GAP = 16             # code points between two links of one word before they count as apart


# --- geometry (a grounding is {page, box} or {html: {source_range}}) --------------------------------------------

def _box(grounding: dict):
    if "box" in grounding:
        return ("box", grounding.get("page", 1), tuple(grounding["box"]))
    if "html" in grounding:
        source_range = grounding["html"]["source_range"]
        return ("range", 0, (source_range["start"], source_range["end"]))
    return None


def _area(shape) -> float:
    kind, _, coords = shape
    if kind == "box":
        return max(0.0, coords[2] - coords[0]) * max(0.0, coords[3] - coords[1])
    return max(0, coords[1] - coords[0])


def _intersection(first, second) -> float:
    if first is None or second is None or first[0] != second[0] or first[1] != second[1]:
        return 0.0
    a, b = first[2], second[2]
    if first[0] == "box":
        return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    return max(0, min(a[1], b[1]) - max(a[0], b[0]))


def ioa(first, second) -> float:
    """Intersection over the FIRST shape's area."""
    area = _area(first) if first else 0
    return _intersection(first, second) / area if area else 0.0


def iou(first, second) -> float:
    inter = _intersection(first, second)
    total = _area(first) + _area(second) - inter
    return inter / total if total else 0.0


def localised(truth_shape, candidate_shape) -> bool:
    return (ioa(truth_shape, candidate_shape) >= LOCALISED_TRUTH_SHARE
            and ioa(candidate_shape, truth_shape) >= LOCALISED_CANDIDATE_SHARE)


def _union(shapes):
    kind, page = shapes[0][0], shapes[0][1]
    coords = [shape[2] for shape in shapes]
    if kind == "box":
        return (kind, page, (min(c[0] for c in coords), min(c[1] for c in coords),
                             max(c[2] for c in coords), max(c[3] for c in coords)))
    return (kind, page, (min(c[0] for c in coords), max(c[1] for c in coords)))


def _far_apart(shapes) -> bool:
    """Two links of one word that sit on different pages/lines, or more than a word-height apart, are ambiguous;
    a word the candidate split into touching pieces ("10", "A") is not."""
    for index, first in enumerate(shapes):
        for second in shapes[index + 1:]:
            if first[0] != second[0] or first[1] != second[1]:
                return True
            a, b = first[2], second[2]
            if first[0] == "range":
                if max(a[0], b[0]) - min(a[1], b[1]) > HTML_AMBIGUITY_GAP:
                    return True
                continue
            gap_x = max(0.0, b[0] - a[2], a[0] - b[2])
            gap_y = max(0.0, b[1] - a[3], a[1] - b[3])
            height = max(a[3] - a[1], b[3] - b[1])
            if gap_y > 0 or gap_x > height:
                return True
    return False


# --- loading ----------------------------------------------------------------------------------------------------

def load_bundle(directory: pathlib.Path) -> tuple[str, dict]:
    document = (directory / "document.md").read_text(encoding="utf-8")
    index_path = directory / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {}
    return document, index


def _contract_problems(document: str, index: dict) -> list[str]:
    """The cheap structural checks of CONTRACT §4 that need no source bytes: ranges in bounds, no overlaps."""
    problems = []
    previous_end = -1
    for word in sorted(index.get("words", []), key=lambda w: w["range"]["start"]):
        start, end = word["range"]["start"], word["range"]["end"]
        if not 0 <= start < end <= len(document):
            problems.append(f"word range {start}-{end} outside the markdown")
        if start < previous_end:
            problems.append(f"word range {start}-{end} overlaps the previous word")
        previous_end = max(previous_end, end)
    return problems[:20]


# --- the five families ------------------------------------------------------------------------------------------

def _aligned_words(truth_words, candidate_words) -> tuple[dict, dict, dict[int, int]]:
    """Text scores and the truth-word → candidate-word map (equal and substituted pairs)."""
    truth_tokens = [mdtext.normalize(word.text) for word in truth_words]
    candidate_tokens = [mdtext.normalize(word.text) for word in candidate_words]
    truth_string, candidate_string = " ".join(truth_tokens), " ".join(candidate_tokens)
    text = {"truth_words": len(truth_tokens), "candidate_words": len(candidate_tokens),
            "ned": round(Levenshtein.normalized_distance(truth_string, candidate_string), 6)}
    pairs: dict[int, int] = {}
    equal: set[int] = set()
    if truth_tokens and candidate_tokens:
        output = jiwer.process_words(truth_string, candidate_string)
        text.update(substitutions=output.substitutions, deletions=output.deletions, insertions=output.insertions,
                    hits=output.hits, wer=round(output.wer, 6))
        for chunk in output.alignments[0]:
            if chunk.type in ("equal", "substitute"):
                for offset in range(chunk.ref_end_idx - chunk.ref_start_idx):
                    pairs[chunk.ref_start_idx + offset] = chunk.hyp_start_idx + offset
                    if chunk.type == "equal":
                        equal.add(chunk.ref_start_idx + offset)
    else:
        text.update(substitutions=0, deletions=len(truth_tokens), insertions=len(candidate_tokens), hits=0,
                    wer=1.0 if truth_tokens else 0.0)
    return text, {"equal": equal}, pairs


def _candidate_links(candidate_words, candidate_index) -> list[list]:
    """For each candidate markdown word, the shapes of the index words whose ranges overlap it."""
    index_words = sorted(((w["range"]["start"], w["range"]["end"], _box(w.get("grounding") or {}))
                          for w in candidate_index.get("words", [])), key=lambda row: row[0])
    links = []
    for word in candidate_words:
        links.append([shape for start, end, shape in index_words
                      if start < word.end and end > word.start and shape is not None])
    return links


def score_location(truth_index, truth_words, candidate_links, pairs) -> dict:
    counts = {"correct": 0, "misplaced": 0, "unlinked": 0, "ambiguous": 0}
    unlinked_missing_text = 0
    ious, examples = [], []
    for truth_position, truth_row in enumerate(truth_index["words"]):
        truth_shape = _box(truth_row["grounding"])
        candidate_position = pairs.get(truth_position)
        shapes = candidate_links[candidate_position] if candidate_position is not None else []
        if candidate_position is None:
            unlinked_missing_text += 1
            outcome = "unlinked"
        elif not shapes:
            outcome = "unlinked"
        elif len(shapes) > 1 and _far_apart(shapes):
            outcome = "ambiguous"
        else:
            shape = _union(shapes)
            ious.append(iou(truth_shape, shape))
            outcome = "correct" if localised(truth_shape, shape) else "misplaced"
        counts[outcome] += 1
        if outcome != "correct" and len(examples) < 25:
            examples.append({"word": truth_words[truth_position].text, "outcome": outcome,
                             "truth_range": truth_row["range"]})
    total = len(truth_index["words"])
    return {**counts, "unlinked_missing_text": unlinked_missing_text,
            "correct_rate": round(counts["correct"] / total, 6) if total else None,
            "misplaced_rate": round(counts["misplaced"] / total, 6) if total else None,
            "median_iou": round(statistics.median(ious), 4) if ious else None,
            "examples": examples}


def score_ledger(truth_extra, candidate_index, candidate_words, candidate_links) -> dict:
    candidate_shapes = [_box(w.get("grounding") or {}) for w in candidate_index.get("words", [])]
    candidate_shapes = [shape for shape in candidate_shapes if shape is not None]
    dropped_regions = [(_box(d.get("grounding") or {}), d.get("reason", "unknown"))
                       for d in candidate_index.get("dropped", [])]
    flagged_regions = [(_box(grounding), block.get("type", "unknown"))
                       for block in candidate_index.get("blocks", []) if block.get("represented") is False
                       for grounding in block.get("grounding", [])]

    fates = {"linked": 0, "lost": 0}
    dropped: dict[str, int] = {}
    flagged: dict[str, int] = {}
    cross = {"wrong_drops": 0, "intended_drops_kept": 0, "intended_drops_lost": 0, "drop_reason_mismatch": 0,
             "flag_missed": 0}
    lost_examples = []
    for source_word in truth_extra["source_words"]:
        shape = _box(source_word["grounding"])
        covered = sum(ioa(shape, candidate) for candidate in candidate_shapes
                      if ioa(candidate, shape) >= LOCALISED_CANDIDATE_SHARE)
        if covered >= LOCALISED_TRUTH_SHARE:
            fate, detail = "linked", None
        else:
            fate, detail = "lost", None
            for region, reason in dropped_regions:
                if region is not None and ioa(shape, region) >= LOCALISED_TRUTH_SHARE:
                    fate, detail = "dropped", reason
                    break
            if fate == "lost":
                for region, kind in flagged_regions:
                    if region is not None and ioa(shape, region) >= LOCALISED_TRUTH_SHARE:
                        fate, detail = "flagged", kind
                        break
        if fate == "dropped":
            dropped[detail] = dropped.get(detail, 0) + 1
        elif fate == "flagged":
            flagged[detail] = flagged.get(detail, 0) + 1
        else:
            fates[fate] += 1
        if fate == "lost" and len(lost_examples) < 25:
            lost_examples.append({"text": source_word["text"], "truth_fate": source_word["fate"]})

        truth_fate = source_word["fate"]
        if truth_fate == "linked" and fate == "dropped":
            cross["wrong_drops"] += 1
        if truth_fate == "dropped":
            if fate == "linked":
                cross["intended_drops_kept"] += 1
            elif fate == "lost":
                cross["intended_drops_lost"] += 1
            elif fate == "dropped" and detail != source_word["reason"]:
                cross["drop_reason_mismatch"] += 1
        if truth_fate == "flagged" and fate not in ("flagged", "linked"):
            cross["flag_missed"] += 1

    unlinked = sum(1 for links in candidate_links if not links)
    unlinked_examples = [word.text for word, links in zip(candidate_words, candidate_links) if not links][:25]
    content = sum(1 for word in truth_extra["source_words"] if word["fate"] == "linked")
    return {"source_words": len(truth_extra["source_words"]), "linked": fates["linked"], "lost": fates["lost"],
            "dropped": dropped, "flagged": flagged, "unlinked": unlinked,
            "markdown_words": len(candidate_words),
            "coverage": round(fates["linked"] / content, 6) if content else None,
            "traceability": round(1 - unlinked / len(candidate_words), 6) if candidate_words else None,
            **cross, "lost_examples": lost_examples, "unlinked_examples": unlinked_examples}


def score_tables(truth_document: str, candidate_document: str) -> tuple[dict, list[tuple[int | None, float]]]:
    truth_tables = tables.tables_from_markdown(truth_document)
    candidate_tables = tables.tables_from_markdown(candidate_document)
    if not truth_tables:
        return {"truth_tables": 0, "candidate_tables": len(candidate_tables)}, []
    matches = tables.match_tables(truth_tables, candidate_tables)
    per_table, structure = [], []
    for truth_position, candidate_position, value in matches:
        per_table.append(round(value, 6))
        structure.append(round(tables.teds(truth_tables[truth_position], candidate_tables[candidate_position],
                                           structure_only=True), 6) if candidate_position is not None else 0.0)
    truth_triples = [triple for table in truth_tables for triple in tables.header_paths(table)]
    remaining = [triple for table in candidate_tables for triple in tables.header_paths(table)]
    hits = 0
    for triple in truth_triples:
        if triple in remaining:
            remaining.remove(triple)
            hits += 1
    summary = {"truth_tables": len(truth_tables), "candidate_tables": len(candidate_tables),
               "teds": per_table, "teds_mean": round(statistics.mean(per_table), 6),
               "teds_structure_mean": round(statistics.mean(structure), 6),
               "header_paths": len(truth_triples),
               "header_path_accuracy": round(hits / len(truth_triples), 6) if truth_triples else None}
    return summary, [(candidate_position, value) for _, candidate_position, value in matches]


def score_ladder(truth_index, truth_extra, truth_words, aligned, table_matches, candidate_index) -> dict:
    blocks = {block["id"]: block for block in truth_index["blocks"]}
    table_ids = [block["id"] for block in truth_index["blocks"] if block["type"] == "table"]
    candidate_blocks = [(_box(grounding), block) for block in candidate_index.get("blocks", [])
                        for grounding in block.get("grounding", [])]
    ladder: dict[str, dict] = {}
    for block_id, kind in truth_extra["ladder"].items():
        block = blocks[block_id]
        row = ladder.setdefault(kind, {"seen": 0, "represented": 0, "truth_represented": 0})
        row["seen"] += 1
        row["truth_represented"] += int(block.get("represented", True))
        if kind == "prose":
            inside = [i for i, word in enumerate(truth_index["words"])
                      if block["range"]["start"] <= word["range"]["start"] < block["range"]["end"]]
            kept = sum(1 for i in inside if i in aligned["equal"])
            represented = bool(inside) and kept / len(inside) >= PROSE_REPRESENTED_SHARE
        elif kind in ("simple_table", "complex_table"):
            position = table_ids.index(block_id)
            represented = position < len(table_matches) and table_matches[position][0] is not None \
                and table_matches[position][1] >= TABLE_REPRESENTED_TEDS
        else:
            # A plot or diagram is represented only when the candidate converts it: a block over the same region
            # that is not marked represented: false.
            truth_shapes = [_box(grounding) for grounding in block["grounding"]]
            represented = any(candidate.get("represented", True) is not False
                              and candidate.get("type") in ("plot", "diagram", "figure", "table")
                              and any(ioa(truth_shape, shape) >= LOCALISED_TRUTH_SHARE for truth_shape in truth_shapes)
                              for shape, candidate in candidate_blocks if shape is not None)
        row["represented"] += int(represented)
    return ladder


def score(truth_dir: pathlib.Path, candidate_dir: pathlib.Path) -> dict:
    truth_document, truth_index = load_bundle(truth_dir)
    truth_extra = json.loads((truth_dir / "truth.json").read_text(encoding="utf-8"))
    candidate_document, candidate_index = load_bundle(candidate_dir)

    truth_words = mdtext.words(truth_document)
    if [(w.start, w.end) for w in truth_words] != [(r["range"]["start"], r["range"]["end"])
                                                   for r in truth_index["words"]]:
        raise SystemExit(f"{truth_dir}: the truth's words are not the tokenizer's words; re-render the case")
    candidate_words = mdtext.words(candidate_document)

    text, aligned, pairs = _aligned_words(truth_words, candidate_words)
    candidate_links = _candidate_links(candidate_words, candidate_index)
    table_scores, table_matches = score_tables(truth_document, candidate_document)
    return {
        "case": truth_extra["case"], "arm": truth_extra["arm"],
        "truth": str(truth_dir), "candidate": str(candidate_dir),
        "contract_problems": _contract_problems(candidate_document, candidate_index),
        "text": text,
        "tables": table_scores,
        "location": score_location(truth_index, truth_words, candidate_links, pairs),
        "ledger": score_ledger(truth_extra, candidate_index, candidate_words, candidate_links),
        "ladder": score_ladder(truth_index, truth_extra, truth_words, aligned, table_matches, candidate_index),
    }


def format_scores(scores: dict) -> str:
    text, table, location, ledger = scores["text"], scores["tables"], scores["location"], scores["ledger"]
    rows = [
        ("text NED", f"{text['ned']:.4f}"),
        ("words S / D / I", f"{text['substitutions']} / {text['deletions']} / {text['insertions']}"
                            f"  (of {text['truth_words']})"),
        ("tables TEDS (structure)", f"{table['teds_mean']:.4f} ({table['teds_structure_mean']:.4f})"
                                    if table.get("teds") else f"no truth tables ({table['candidate_tables']} found)"),
        ("header-path accuracy", f"{table['header_path_accuracy']:.4f}" if table.get("header_path_accuracy")
                                 is not None else "-"),
        ("location correct / misplaced / unlinked / ambiguous",
         f"{location['correct']} / {location['misplaced']} / {location['unlinked']} / {location['ambiguous']}"),
        ("location median IoU (not gated)", "-" if location["median_iou"] is None else f"{location['median_iou']}"),
        ("ledger linked / lost / unlinked", f"{ledger['linked']} / {ledger['lost']} / {ledger['unlinked']}"),
        ("ledger dropped / flagged", f"{ledger['dropped'] or '-'} / {ledger['flagged'] or '-'}"),
        ("wrong drops / drops kept / drops lost",
         f"{ledger['wrong_drops']} / {ledger['intended_drops_kept']} / {ledger['intended_drops_lost']}"),
        ("ladder (represented / seen)", ", ".join(f"{kind} {row['represented']}/{row['seen']}"
                                                  for kind, row in scores["ladder"].items())),
    ]
    width = max(len(name) for name, _ in rows)
    header = f"{scores['case']} · {scores['arm']}"
    return "\n".join([header, "-" * len(header)] + [f"{name:<{width}}  {value}" for name, value in rows])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("truth_dir", type=pathlib.Path, help="harness/out/<case>/<arm>")
    parser.add_argument("candidate_dir", type=pathlib.Path, help="the candidate bundle directory")
    parser.add_argument("--out", type=pathlib.Path, help="where scores.json goes (default: in the candidate dir)")
    arguments = parser.parse_args(argv)
    scores = score(arguments.truth_dir, arguments.candidate_dir)
    out = arguments.out or arguments.candidate_dir / "scores.json"
    if out.resolve().parent == arguments.truth_dir.resolve() and not arguments.out:
        out = None   # scoring the truth against itself: never write into the truth bundle
    if out is not None:
        out.write_text(json.dumps(scores, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(format_scores(scores))
    return 0


if __name__ == "__main__":
    sys.exit(main())
