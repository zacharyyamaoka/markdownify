"""The coverage ledger (CONTRACT §5) and the KPIs (§6), computed the same way for every converter.

WHY here and not in the converters: the ledger is the grade, and a converter must not grade itself. The core
computes it from two things only — the span index the converter produced, and the source inventory, which each
converter module reads WITHOUT its own conversion code path (`inventory()`: pdfplumber words for a PDF, the page's
visible text nodes for HTML). `mdify check` calls the same `fates_of` on the saved index to re-derive invariant 3.

A source word's fate is decided by geometry, not by the aligner's bookkeeping, so any converter's index can be graded:
- `linked`: some markdown word's grounding overlaps it (boxes: the overlap covers at least half of the smaller box;
  HTML: the source ranges intersect);
- `dropped`: its centre lies in a region the converter left out on purpose, with that region's reason;
- `flagged`: its centre lies in a `represented: false` block (a figure, a plot), with the block's type;
- `lost`: none of the above — the KPI that must be 0.
"""

from __future__ import annotations

from .converters import SourceWord

SCHEMA = "markdownify/ledger@0"
KPI_SCHEMA = "markdownify/kpis@0"
PROSE_TYPES = {"heading", "paragraph", "list_item", "caption", "footnote", "code"}
FIGURE_TYPES = {"figure", "plot", "diagram"}
_BANDS = 64          # y-bands of the per-page grid that finds overlapping word boxes quickly
_MIN_SIDE = 1e-4     # a zero-height box (a rule, a degenerate glyph) still has an area


# --- geometry ---------------------------------------------------------------------------------------------------
def _box(grounding: dict) -> tuple[int, list[float]] | None:
    if grounding and "box" in grounding and grounding.get("page"):
        return grounding["page"], grounding["box"]
    return None


def _range(grounding: dict) -> tuple[int, int] | None:
    source_range = ((grounding or {}).get("html") or {}).get("source_range")
    if source_range:
        return source_range["start"], source_range["end"]
    return None


def _area(box: list[float]) -> float:
    return max(box[2] - box[0], _MIN_SIDE) * max(box[3] - box[1], _MIN_SIDE)


def _overlap(a: list[float], b: list[float]) -> float:
    width = min(a[2], b[2]) - max(a[0], b[0])
    height = min(a[3], b[3]) - max(a[1], b[1])
    if width < -_MIN_SIDE or height < -_MIN_SIDE:
        return 0.0
    return max(width, _MIN_SIDE) * max(height, _MIN_SIDE)


def _center_inside(box: list[float], region: list[float]) -> bool:
    center_x, center_y = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return region[0] <= center_x <= region[2] and region[1] <= center_y <= region[3]


class _Groundings:
    """Markdown word groundings indexed for "does anything overlap this source word?"."""

    def __init__(self, groundings: list[dict]):
        self.grid: dict[tuple[int, int], list[list[float]]] = {}
        self.ranges: list[tuple[int, int]] = []
        for grounding in groundings:
            located = _box(grounding)
            if located:
                page, box = located
                for band in self._bands(box):
                    self.grid.setdefault((page, band), []).append(box)
            elif _range(grounding):
                self.ranges.append(_range(grounding))
        self.ranges.sort()

    @staticmethod
    def _bands(box: list[float]) -> range:
        low = min(max(int(box[1] * _BANDS), 0), _BANDS - 1)
        high = min(max(int(box[3] * _BANDS), 0), _BANDS - 1)
        return range(low, high + 1)

    def overlaps(self, grounding: dict) -> bool:
        located = _box(grounding)
        if located:
            page, box = located
            for band in self._bands(box):
                for other in self.grid.get((page, band), []):
                    if _overlap(box, other) >= 0.5 * min(_area(box), _area(other)):
                        return True
            return False
        source_range = _range(grounding)
        if source_range:
            start, end = source_range
            return any(other_start < end and start < other_end for other_start, other_end in self.ranges)
        return False


def _region_of(grounding: dict, regions: list[tuple[dict, str]]) -> str | None:
    located = _box(grounding)
    source_range = _range(grounding)
    for region, label in regions:
        region_box = _box(region)
        if located and region_box and located[0] == region_box[0] and _center_inside(located[1], region_box[1]):
            return label
        region_range = _range(region)
        if source_range and region_range:
            middle = (source_range[0] + source_range[1]) / 2
            if region_range[0] <= middle < region_range[1]:
                return label
    return None


# --- fates ------------------------------------------------------------------------------------------------------
def fates_of(index: dict, inventory: list[SourceWord]) -> list[str]:
    """One fate per inventory word, in order: "linked", "dropped:<reason>", "flagged:<kind>" or "lost"."""
    linked = _Groundings([word["grounding"] for word in index["words"] if word.get("grounding")])
    dropped = [(region["grounding"], region["reason"]) for region in index.get("dropped", [])]
    flagged = [(grounding, block["type"]) for block in index["blocks"] if block.get("represented") is False
               for grounding in block.get("grounding", [])]
    fates = []
    for word in inventory:
        if linked.overlaps(word.grounding):
            fates.append("linked")
            continue
        reason = _region_of(word.grounding, dropped)
        if reason:
            fates.append(f"dropped:{reason}")
            continue
        kind = _region_of(word.grounding, flagged)
        fates.append(f"flagged:{kind}" if kind else "lost")
    return fates


def compute(index: dict, markdown: str, inventory: list[SourceWord] | None, inventory_name: str) -> dict:
    from . import align

    source_side: dict = {"inventory": inventory_name, "available": inventory is not None}
    if inventory is not None:
        fates = fates_of(index, inventory)
        dropped_by_reason: dict[str, int] = {}
        flagged_by_kind: dict[str, int] = {}
        for fate in fates:
            if fate.startswith("dropped:"):
                reason = fate.split(":", 1)[1]
                dropped_by_reason[reason] = dropped_by_reason.get(reason, 0) + 1
            elif fate.startswith("flagged:"):
                kind = fate.split(":", 1)[1]
                flagged_by_kind[kind] = flagged_by_kind.get(kind, 0) + 1
        source_side.update({
            "words": len(fates),
            "linked": fates.count("linked"),
            "dropped": sum(dropped_by_reason.values()),
            "flagged": sum(flagged_by_kind.values()),
            "lost": fates.count("lost"),
            "dropped_by_reason": dropped_by_reason,
            "flagged_by_kind": flagged_by_kind,
            "lost_words": [{"text": word.text, "grounding": word.grounding}
                           for word, fate in zip(inventory, fates) if fate == "lost"],
            "fates": fates,
        })

    mask = align.content_mask(markdown)
    unlinked, inexact = [], []
    for word in index["words"]:
        text = align.word_text(markdown, word["range"]["start"], word["range"]["end"], mask)
        if not word.get("grounding"):
            unlinked.append({"range": word["range"], "text": text})
        elif word.get("score", 0) < 1.0:
            entry = {"range": word["range"], "text": text, "score": word["score"], "grounding": word["grounding"]}
            if word.get("note"):
                entry["note"] = word["note"]
            inexact.append(entry)
    markdown_side = {
        "words": len(index["words"]),
        "linked": len(index["words"]) - len(unlinked),
        "unlinked": len(unlinked),
        "inexact": len(inexact),
        "unlinked_words": unlinked,
        "inexact_words": inexact,
    }
    return {"schema": SCHEMA, "source": source_side, "markdown": markdown_side}


# --- KPIs -------------------------------------------------------------------------------------------------------
def _table_is_complex(table: dict, cells: list[dict]) -> bool:
    """Merged cells, or more than one row of column headers (a header that groups models)."""
    if any(cell.get("rowspan", 1) > 1 or cell.get("colspan", 1) > 1 for cell in cells):
        return True
    header_rows = {cell["row"] for cell in cells if cell.get("kind") == "columnHeader"}
    return len(header_rows) > 1


def ladder_of(index: dict) -> dict:
    by_id = {block["id"]: block for block in index["blocks"]}
    ladder = {kind: {"seen": 0, "represented": 0}
              for kind in ("prose", "simple_table", "complex_table", "plot", "diagram", "figure")}
    for block in index["blocks"]:
        kind = None
        if block["type"] in PROSE_TYPES:
            kind = "prose"
        elif block["type"] == "table":
            cells = [by_id[child] for child in block.get("children", []) if child in by_id]
            kind = "complex_table" if _table_is_complex(block, cells) else "simple_table"
        elif block["type"] in FIGURE_TYPES:
            kind = block["type"]
        if kind:
            ladder[kind]["seen"] += 1
            ladder[kind]["represented"] += 1 if block.get("represented", True) else 0
    for counts in ladder.values():
        counts["ratio"] = round(counts["represented"] / counts["seen"], 4) if counts["seen"] else None
    return ladder


def kpis_of(index: dict, ledger: dict, timing: dict) -> dict:
    source, markdown = ledger["source"], ledger["markdown"]
    coverage = None
    if source.get("available"):
        denominator = source["words"] - source["dropped"] - source["flagged"]
        coverage = round(source["linked"] / denominator, 4) if denominator else None
    pages = max(len(index.get("pages", [])), 1)
    return {
        "schema": KPI_SCHEMA,
        "silently_lost": source.get("lost") if source.get("available") else None,
        "unlinked": markdown["unlinked"],
        "coverage": coverage,
        "traceability": round(markdown["linked"] / markdown["words"], 4) if markdown["words"] else None,
        "inexact_words": markdown["inexact"],
        "ladder": ladder_of(index),
        "time": {**timing, "pages": len(index.get("pages", [])),
                 "seconds_per_page": round(timing["seconds"] / pages, 3)},
    }
