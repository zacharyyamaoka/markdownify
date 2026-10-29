"""The `snapshot` capture level's words (CONTRACT §2.1, §3.3): every visible word's box as the browser measured it,
stored beside `snapshot.jpg`, and each converter word's `snapshot` grounding derived from them.

`snapshot.json` (compact; one row per measured word):

    {"schema": "markdownify/snapshot@0", "image": "snapshot.jpg", "width": <px>, "height": <px>,
     "css_width": …, "css_height": …, "xpaths": ["/html/body/…", …],
     "words":  [[text, xpath_index, ordinal, x0, y0, x1, y1(, parts)], …],   # boxes 0–1 of the image, top-left
     "images": [[alt, xpath_index, x0, y0, x1, y1], …]}               # <img alt>: the converter writes alt text

`parts`, only for a word written across several text nodes ("<b>9</b>Nm"): [[characters, box or null], …] per node.

How a converter word finds its box (`attach`): its grounding's `source_range` names the inventory word(s) it came
from, exactly; each inventory word's key is its xpath and its ordinal among that xpath's words, which the browser's
walk (`mdify.capture_browser`) computes the same way; the key gives the measured box. WHY keys and not text: the
xpath + offset is already exact, while a text match on CubeMars would pin a spec cell to one of the menu's many
copies of the same model name. Only where the page's scripts changed the DOM (so a key's text no longer agrees) does
a word fall back to the order of the text: runs of at least `MIN_RUN` equal words in the same order.

A markdown word covering part of a source word gets the boxes of the text nodes it covers (`parts`), and inside a
node its share of the width by character count (WHY: one box per text node is what the browser measured;
per-character boxes would triple snapshot.json for the rare partial word).
"""

from __future__ import annotations

import bisect
import difflib
import functools
import io
import json
import pathlib

from . import align

SCHEMA = "markdownify/snapshot@0"
IMAGE_NAME = "snapshot.jpg"
WORDS_NAME = "snapshot.json"
DIGITS = 5          # 0–1 coordinates to 1e-5: a fifth of a pixel on a 20 000 px tall page
MIN_RUN = 3         # fallback: a lone equal word is a coincidence; three in order are the same text
_OVERLAP = 0.5      # a measured word lies under a box when their overlap covers half of the smaller one


# --- writing ----------------------------------------------------------------------------------------------------
def words_file(measured: dict, image_jpeg: bytes) -> dict:
    """snapshot.json's content from the browser's walk and the screenshot it describes."""
    from PIL import Image

    with Image.open(io.BytesIO(image_jpeg)) as image:
        width, height = image.size
    xpaths: dict[str, int] = {}

    def number(xpath: str) -> int:
        return xpaths.setdefault(xpath, len(xpaths))

    def scaled(box) -> list[float]:
        x0, y0, x1, y1 = box
        return [round(min(max(x0 / width, 0.0), 1.0), DIGITS), round(min(max(y0 / height, 0.0), 1.0), DIGITS),
                round(min(max(x1 / width, 0.0), 1.0), DIGITS), round(min(max(y1 / height, 0.0), 1.0), DIGITS)]

    # WHY divide CSS px by image px: the context runs at device_scale_factor 1, so one CSS px is one image px; the
    # page's own size is recorded too, so a screenshot Chromium cropped (> 16 384 px wide, say) shows up as a mismatch.
    words = []
    for text, xpath, ordinal, box, *parts in measured["words"]:
        row = [text, number(xpath), ordinal, *scaled(box)]
        if parts:
            row.append([[characters, scaled(part) if part else None] for characters, part in parts[0]])
        words.append(row)
    images = [[alt, number(xpath), *scaled(box)] for alt, xpath, box in measured["images"]]
    return {"schema": SCHEMA, "image": IMAGE_NAME, "width": width, "height": height,
            "css_width": measured["css_width"], "css_height": measured["css_height"], "walked": measured["walked"],
            "xpaths": list(xpaths), "words": words, "images": images}


def dump(content: dict) -> bytes:
    return json.dumps(content, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


# --- attaching boxes to a conversion -------------------------------------------------------------------------------
def attach(index: dict, source, directory: pathlib.Path) -> dict:
    """Add `snapshot: {page: 1, box}` to every word grounding of `index` that has a measured box (in place).
    Returns counts: {linked, boxed, by_key, by_order, no_box}."""
    from . import html

    snapshot = json.loads((pathlib.Path(directory) / WORDS_NAME).read_text(encoding="utf-8"))
    xpaths = snapshot["xpaths"]
    page = html.visible_page(source)
    inventory = page.words

    measured: dict[tuple[str, int], tuple[str, list]] = {}
    for row in snapshot["words"]:
        measured[(xpaths[row[1]], row[2])] = (row[0], row[3:])
    image_boxes = {f"{xpaths[xpath_index]}/@alt": (alt, box) for alt, xpath_index, *box in snapshot["images"]}

    # Inventory word → box: by key first, then by text order for whatever the page's scripts moved.
    boxes: dict[int, list[float]] = {}
    how: dict[int, str] = {}
    ordinals: dict[str, int] = {}
    for position, word in enumerate(inventory):
        ordinal = ordinals.get(word["xpath"], 0)
        ordinals[word["xpath"]] = ordinal + 1
        found = measured.get((word["xpath"], ordinal))
        if found and found[0] == word["text"]:
            boxes[position], how[position] = found[1], "key"
    if len(boxes) < len(inventory):
        rows = snapshot["words"]
        matcher = difflib.SequenceMatcher(None, [word["text"] for word in inventory], [row[0] for row in rows],
                                          autojunk=False)
        for block in matcher.get_matching_blocks():
            if block.size < MIN_RUN:
                continue
            for offset in range(block.size):
                position = block.a + offset
                if position not in boxes:
                    boxes[position], how[position] = rows[block.b + offset][3:], "order"

    order = sorted(range(len(inventory)), key=lambda position: inventory[position]["source_range"]["start"])
    starts = [inventory[position]["source_range"]["start"] for position in order]
    counts = {"linked": 0, "boxed": 0, "by_key": 0, "by_order": 0, "no_box": 0}
    for word in index["words"]:
        grounding = word.get("grounding")
        if not grounding or "html" not in grounding:
            continue
        counts["linked"] += 1
        found = _box_of(grounding["html"], page, order, starts, boxes, how, image_boxes)
        if found is None:
            counts["no_box"] += 1
            continue
        box, ways = found
        grounding["snapshot"] = {"page": 1, "box": box}
        counts["boxed"] += 1
        counts["by_order" if "order" in ways else "by_key"] += 1
    return counts


def _box_of(located: dict, page, order, starts, boxes, how, image_boxes):
    """The snapshot box of one §3.3 grounding, or None when any part of it has no measured box."""
    xpath = located.get("xpath") or ""
    if xpath.endswith("/@alt"):
        image = image_boxes.get(xpath)
        return (image[1], {"key"}) if image else None
    start, end = located["source_range"]["start"], located["source_range"]["end"]
    first = max(bisect.bisect_right(starts, start) - 1, 0)
    parts, ways = [], set()
    for k in range(first, len(order)):
        position = order[k]
        word_range = page.words[position]["source_range"]
        if word_range["start"] >= end:
            break
        if word_range["end"] <= start:
            continue
        if position not in boxes:
            return None
        offsets = page.char_offsets(position)
        covered = [i for i, (low, high) in enumerate(offsets) if high > start and low < end]
        if not covered:
            continue
        box = _covered_box(boxes[position], covered[0], covered[-1] + 1, len(offsets))
        if box is None:
            return None
        parts.append(box)
        ways.add(how[position])
    if not parts:
        return None
    box = [min(part[0] for part in parts), min(part[1] for part in parts),
           max(part[2] for part in parts), max(part[3] for part in parts)]
    return [round(value, DIGITS) for value in box], ways


def _covered_box(measured: list, first: int, last: int, length: int) -> list[float] | None:
    """The box of characters [first, last) of a measured word of `length` characters: the whole box, or the boxes
    of the text nodes (parts) those characters were written in, cut by character share inside a node."""
    box, node_parts = measured[:4], (measured[4] if len(measured) > 4 else None)
    if (first, last) == (0, length):
        return list(box)
    if not node_parts or sum(characters for characters, _ in node_parts) != length:
        node_parts = [[length, box]]
    pieces, start = [], 0
    for characters, part in node_parts:
        end = start + characters
        low, high = max(first, start), min(last, end)
        if low < high:
            if part is None:
                return None
            x0, y0, x1, y1 = part
            share = (x1 - x0) / characters
            pieces.append([x0 + share * (low - start), y0, x0 + share * (high - start), y1])
        start = end
    if not pieces:
        return None
    return [min(p[0] for p in pieces), min(p[1] for p in pieces), max(p[2] for p in pieces), max(p[3] for p in pieces)]


# --- checking (mdify check, through mdify.html.text_under) -------------------------------------------------------------
class _Measured:
    """snapshot.json's words in horizontal bands, for "which words lie under this box?"."""
    BANDS = 200

    def __init__(self, content: dict):
        self.rows = [(row[0], row[3:7]) for row in content["words"]]
        self.rows += [(alt, box) for alt, _xpath, *box in content["images"]]
        self.bands: list[list[int]] = [[] for _ in range(self.BANDS)]
        for number, (_text, box) in enumerate(self.rows):
            for band in self._bands(box):
                self.bands[band].append(number)

    def _bands(self, box) -> range:
        low = min(int(box[1] * self.BANDS), self.BANDS - 1)
        high = min(int(box[3] * self.BANDS), self.BANDS - 1)
        return range(low, high + 1)

    def text_under(self, box) -> str:
        seen = set()
        for band in self._bands(box):
            seen.update(self.bands[band])
        under = []
        for number in sorted(seen):
            text, measured = self.rows[number]
            if _overlaps(measured, box):
                under.append(text)
        return " ".join(under)


def _area(box) -> float:
    return max(box[2] - box[0], 0.0) * max(box[3] - box[1], 0.0)


def _overlaps(a, b) -> bool:
    inter = [max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])]
    smaller = min(_area(a), _area(b))
    return smaller > 0 and _area(inter) >= _OVERLAP * smaller


@functools.lru_cache(maxsize=4)
def _load(path: str, mtime_ns: int) -> _Measured | None:
    file = pathlib.Path(path)
    return _Measured(json.loads(file.read_text(encoding="utf-8"))) if file.is_file() else None


def disagreement(directory: pathlib.Path, snapshot_grounding: dict, text: str) -> str | None:
    """None when the measured words under `snapshot_grounding`'s box contain `text` (NFKC, whitespace ignored);
    otherwise what the box holds instead, for `mdify check` to quote."""
    path = pathlib.Path(directory) / WORDS_NAME
    if not path.is_file():
        return f"no {WORDS_NAME} beside the source"
    measured = _load(str(path), path.stat().st_mtime_ns)
    under = measured.text_under(snapshot_grounding["box"])
    if align.normalize(text) and align.normalize(text) in align.normalize(under):
        return None
    return under or "(no measured word)"
