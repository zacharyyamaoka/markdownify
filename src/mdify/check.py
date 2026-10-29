"""`mdify check <bundle>`: re-derive the five invariants of CONTRACT §4 from the cached bytes. No model, no network.

Each invariant is re-computed from the files, never read from a converter's claim:
1. Faithful — every linked word's markdown text equals the text-layer characters under its box (the converter
   module's `text_under`, which reads the source without the conversion code path), after NFKC and whitespace
   normalisation; or its score is below 1.0 and the ledger lists it. Sources without a text layer (images) have
   nothing independent to compare with: those words are counted as not checkable, not as passing.
2. Traceable — every content character of document.md (mdify.align's content mask) lies inside some word's range.
3. Accounted for — the inventory, re-read from the bytes, gives every source word exactly the fate the ledger records.
4. Well formed — word ranges in order and never overlapping; block ranges nest; every cell inside its table, its
   row/col inside the table's size; boxes inside the page.
5. Pinned — source.<ext> hashes to source.json's sha256 (and index.json's), document.md to markdown_sha256.
"""

from __future__ import annotations

import dataclasses
import hashlib
import pathlib

from . import align, bundle, ledger as ledger_module
from .cache import sha256_file

_SHOWN = 10  # failures quoted per invariant; the count is always complete


BUNDLE_FILES = ("source.json", "document.md", "index.json", "ledger.json")


class IncompleteBundle(Exception):
    """The directory is not a whole bundle (a cache directory holding only the source, after an `--out` run)."""


@dataclasses.dataclass
class Result:
    number: int
    name: str
    ok: bool
    summary: str
    failures: list[str] = dataclasses.field(default_factory=list)


def check(directory: pathlib.Path) -> list[Result]:
    directory = pathlib.Path(directory)
    if not directory.is_dir():
        raise IncompleteBundle(f"{directory} is not a directory")
    missing = [name for name in BUNDLE_FILES if not (directory / name).is_file()]
    if missing:
        only_source = "source.json" not in missing and len(missing) == len(BUNDLE_FILES) - 1
        raise IncompleteBundle(f"not a complete bundle: missing {', '.join(missing)}"
                               + (" (source only)" if only_source else ""))
    record, index, ledger, markdown, source = bundle.load(directory)
    return [_faithful(index, ledger, markdown, source), _traceable(index, markdown), _accounted(index, ledger, source),
            _well_formed(index, markdown), _pinned(directory, record, index, source)]


def _faithful(index, ledger, markdown, source) -> Result:
    module = bundle.converter_for(source.media_type)
    mask = align.content_mask(markdown)
    listed = {(entry["range"]["start"], entry["range"]["end"]) for entry in ledger["markdown"].get("inexact_words", [])}
    exact = inexact = unchecked = 0
    failures = []
    for word in index["words"]:
        if not word.get("grounding"):
            continue
        start, end = word["range"]["start"], word["range"]["end"]
        text = align.word_text(markdown, start, end, mask)
        under = module.text_under(source, word["grounding"])
        if under is None:
            unchecked += 1
            continue
        if align.normalize(text) == align.normalize(under):
            exact += 1
        elif word.get("score", 0) < 1.0 and (start, end) in listed:
            inexact += 1
        else:
            failures.append(f"{text!r} at {start}–{end} (score {word.get('score')}) but the source has {under!r} "
                            f"under {word['grounding']}")
    summary = f"{exact} exact, {inexact} below 1.0 and listed, {unchecked} not checkable (no text layer)"
    return Result(1, "faithful", not failures, summary, failures)


def _traceable(index, markdown) -> Result:
    mask = align.content_mask(markdown)
    change = [0] * (len(markdown) + 1)  # +1 where a word starts, -1 where it ends
    for word in index["words"]:
        change[min(word["range"]["start"], len(markdown))] += 1
        change[min(word["range"]["end"], len(markdown))] -= 1
    inside = [False] * len(markdown)
    depth = 0
    for position in range(len(markdown)):
        depth += change[position]
        inside[position] = depth > 0
    failures = []
    total = 0
    for position, character in enumerate(markdown):
        if mask[position] == align.CONTENT and not character.isspace():
            total += 1
            if not inside[position]:
                failures.append(f"{character!r} at {position} (…{markdown[max(position - 20, 0):position + 20]!r}…)")
    return Result(2, "traceable", not failures, f"{total - len(failures)} of {total} content characters inside a word",
                  failures)


def _accounted(index, ledger, source) -> Result:
    module = bundle.converter_for(source.media_type)
    inventory = module.inventory(source)
    recorded = ledger["source"]
    if inventory is None:
        ok = not recorded.get("available")
        return Result(3, "accounted", ok, "no inventory for this source (CONTRACT §5)" if ok else
                      "the ledger claims an inventory, but the source has none", [] if ok else ["inventory mismatch"])
    fates = ledger_module.fates_of(index, inventory)
    failures = []
    if len(fates) != len(recorded.get("fates", [])):
        failures.append(f"inventory has {len(fates)} words, the ledger {len(recorded.get('fates', []))}")
    else:
        for position, (word, fate, claimed) in enumerate(zip(inventory, fates, recorded["fates"])):
            if fate != claimed:
                failures.append(f"word {position} {word.text!r}: re-derived {fate}, ledger says {claimed}")
    counts = {}
    for fate in fates:
        counts[fate.split(":")[0]] = counts.get(fate.split(":")[0], 0) + 1
    summary = f"{len(fates)} source words: " + ", ".join(f"{counts.get(key, 0)} {key}"
                                                           for key in ("linked", "dropped", "flagged", "lost"))
    return Result(3, "accounted", not failures, summary, failures)


def _well_formed(index, markdown) -> Result:
    failures = []
    length = len(markdown)
    previous_end = 0
    for word in index["words"]:
        start, end = word["range"]["start"], word["range"]["end"]
        if not 0 <= start < end <= length:
            failures.append(f"word range {start}–{end} outside the markdown (length {length})")
        if start < previous_end:
            failures.append(f"word range {start}–{end} overlaps or precedes the previous word (ends {previous_end})")
        previous_end = max(previous_end, end)
        failures.extend(_box_failures(word.get("grounding"), index, f"word {start}–{end}"))

    blocks = index["blocks"]
    by_id = {}
    for block in blocks:
        if block["id"] in by_id:
            failures.append(f"duplicate block id {block['id']}")
        by_id[block["id"]] = block
        start, end = block["range"]["start"], block["range"]["end"]
        if not 0 <= start <= end <= length:
            failures.append(f"{block['id']} range {start}–{end} outside the markdown")
        for grounding in block.get("grounding", []):
            failures.extend(_box_failures(grounding, index, block["id"]))
    ordered = sorted(blocks, key=lambda block: (block["range"]["start"], -block["range"]["end"]))
    stack: list[dict] = []
    for block in ordered:
        start, end = block["range"]["start"], block["range"]["end"]
        while stack and stack[-1]["range"]["end"] <= start and not (stack[-1]["range"]["end"] == start == end):
            stack.pop()
        if stack and end > stack[-1]["range"]["end"] and start < stack[-1]["range"]["end"]:
            failures.append(f"{block['id']} ({start}–{end}) crosses {stack[-1]['id']} "
                            f"({stack[-1]['range']['start']}–{stack[-1]['range']['end']})")
        if start < end:
            stack.append(block)
    for table in (block for block in blocks if block["type"] == "table"):
        for child in table.get("children", []):
            cell = by_id.get(child)
            if cell is None:
                failures.append(f"{table['id']} lists a missing child {child}")
                continue
            if not (table["range"]["start"] <= cell["range"]["start"] <= cell["range"]["end"] <= table["range"]["end"]):
                failures.append(f"{child} lies outside {table['id']}")
            rows, cols = table.get("rows"), table.get("cols")
            if rows is not None and not (0 <= cell["row"] and cell["row"] + cell.get("rowspan", 1) <= rows):
                failures.append(f"{child} row {cell['row']}+{cell.get('rowspan', 1)} does not fit {rows} rows")
            if cols is not None and not (0 <= cell["col"] and cell["col"] + cell.get("colspan", 1) <= cols):
                failures.append(f"{child} col {cell['col']}+{cell.get('colspan', 1)} does not fit {cols} cols")
    cells = sum(1 for block in blocks if block["type"] == "table_cell")
    return Result(4, "well formed", not failures, f"{len(index['words'])} words, {len(blocks)} blocks ({cells} cells)",
                  failures)


def _box_failures(grounding, index, label) -> list[str]:
    if not grounding or "box" not in grounding:
        return []
    pages = {page["page"] for page in index.get("pages", [])}
    x0, y0, x1, y1 = grounding["box"]
    failures = []
    if pages and grounding.get("page") not in pages:
        failures.append(f"{label}: page {grounding.get('page')} is not in the source")
    if not (0 <= x0 <= x1 <= 1 and 0 <= y0 <= y1 <= 1):
        failures.append(f"{label}: box {grounding['box']} is not inside the page")
    return failures


def _pinned(directory, record, index, source) -> Result:
    failures = []
    if source.path is None or not source.path.is_file():
        failures.append(f"{directory}: no source.<ext>")
    else:
        actual = sha256_file(source.path)
        if actual != record["sha256"]:
            failures.append(f"{source.path.name} hashes to {actual}, source.json says {record['sha256']}")
    if index["source"]["sha256"] != record["sha256"]:
        failures.append(f"index.json's source sha256 {index['source']['sha256']} is not source.json's")
    markdown_hash = hashlib.sha256((pathlib.Path(directory) / "document.md").read_bytes()).hexdigest()
    if markdown_hash != index["markdown_sha256"]:
        failures.append(f"document.md hashes to {markdown_hash}, index.json says {index['markdown_sha256']}")
    return Result(5, "pinned", not failures, "source and markdown hashes match" if not failures else "hash mismatch",
                  failures)


def report(directory: pathlib.Path, results: list[Result]) -> str:
    lines = [f"mdify check {directory}"]
    for result in results:
        lines.append(f"  {result.number} {result.name:<12} {'PASS' if result.ok else 'FAIL'}  {result.summary}")
        for failure in result.failures[:_SHOWN]:
            lines.append(f"      - {failure}")
        if len(result.failures) > _SHOWN:
            lines.append(f"      … and {len(result.failures) - _SHOWN} more")
    return "\n".join(lines)
