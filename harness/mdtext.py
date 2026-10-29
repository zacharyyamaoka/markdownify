"""Markdown words as a reader reads them: the ruler's own tokenizer and text normaliser.

WHY the harness has its own instead of importing `mdify.align`: the harness grades mdify, so it may not share
mdify's code. A bug shared by the ruler and the thing it measures cancels out and is never seen. Both follow the
same contract rule (CONTRACT §3.2: markdown syntax is never a word), written twice on purpose.

Every code point of the markdown is one of:
- CONTENT: text a reader reads;
- SYNTAX: markdown that is invisible but does not split a word (`**`, a backslash escape, backticks);
- BREAK: splits words the way whitespace does (table pipes, a raw-HTML tag, a link's URL, a heading's `#`).

A word is a maximal run of CONTENT, possibly with SYNTAX inside it; its range runs from its first to its last
content character, so `**9**` is the word "9" over the `9` only, and `X1\\_A` is the word "X1_A" over `X1\\_A`.

The truth bundle's words are produced by this tokenizer and then checked against what Chrome actually drew
(render.py fails a case when a CONTENT character has no glyph or a glyph has no CONTENT character). So the
tokenizer cannot silently disagree with the rendered page.
"""

from __future__ import annotations

import dataclasses
import re
import unicodedata

CONTENT, SYNTAX, BREAK = 0, 1, 2

_TAG = re.compile(r"<!--.*?-->|</?[A-Za-z][A-Za-z0-9-]*(?:\s[^<>]*)?/?>", re.S)
_HEADING = re.compile(r"^ {0,3}#{1,6}(?=\s|$)", re.M)
_CLOSING_HASHES = re.compile(r"\s#+[ \t]*$", re.M)
_QUOTE = re.compile(r"^ {0,3}(?:>\s?)+", re.M)
_BULLET = re.compile(r"^\s*(?:[-*+]|\d{1,9}[.)])(?=\s)", re.M)
_TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)*\|?\s*$", re.M)
_THEMATIC_BREAK = re.compile(r"^ {0,3}([-*_])(?:\s*\1){2,}\s*$", re.M)
_FENCE = re.compile(r"^ {0,3}(?:`{3,}|~{3,}).*$", re.M)
_IMAGE = re.compile(r"!\[[^\]\n]*\]\([^)\n]*\)")
_LINK_TAIL = re.compile(r"\]\((?:[^()\s]*|<[^>\n]*>)(?:\s+\"[^\"\n]*\")?\)")
_ASCII_PUNCTUATION = set("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")

# Folds after NFKC, copied in spirit from olmOCR's normalize_text (research 3-evaluation.md, Metrics §a): the
# same value typed with a different dash or quote is the same value.
_FOLDS = str.maketrans({
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "−": "-",
    "‘": "'", "’": "'", "‚": "'", "“": '"', "”": '"', "„": '"',
})
_INVISIBLE = dict.fromkeys(map(ord, "­​‌‍⁠﻿"))


@dataclasses.dataclass(frozen=True)
class Word:
    start: int      # code point offset into the markdown; end exclusive
    end: int
    text: str       # the content characters only


def content_mask(markdown: str) -> bytearray:
    """CONTENT / SYNTAX / BREAK for every code point of `markdown`."""
    mask = bytearray(len(markdown))
    for position, character in enumerate(markdown):
        if character.isspace():
            mask[position] = BREAK

    escaped: set[int] = set()   # positions of characters made literal by a backslash
    position = 0
    while position < len(markdown) - 1:
        if markdown[position] == "\\" and markdown[position + 1] in _ASCII_PUNCTUATION:
            mask[position] = SYNTAX
            escaped.add(position + 1)
            position += 2
        else:
            position += 1

    def mark(start: int, end: int, kind: int) -> None:
        for index in range(start, end):
            if index not in escaped and not markdown[index].isspace():
                mask[index] = kind

    # Whole-line syntax first: separator rows, thematic breaks and code fences carry no text.
    for pattern in (_TABLE_SEPARATOR, _THEMATIC_BREAK, _FENCE):
        for match in pattern.finditer(markdown):
            mark(match.start(), match.end(), BREAK)
    for pattern in (_HEADING, _CLOSING_HASHES, _QUOTE, _BULLET):
        for match in pattern.finditer(markdown):
            mark(match.start(), match.end(), BREAK)
    for pattern in (_TAG, _IMAGE, _LINK_TAIL):
        for match in pattern.finditer(markdown):
            if match.start() not in escaped:
                mark(match.start(), match.end(), BREAK)

    for position, character in enumerate(markdown):
        if position in escaped or mask[position] != CONTENT:
            continue
        if character == "|":
            mask[position] = BREAK
        elif character in "*`~":
            mask[position] = SYNTAX
        elif character == "_":
            # Intraword underscores are literal in CommonMark (`KM80_9`); emphasis underscores touch a non-word side.
            before = markdown[position - 1] if position else " "
            after = markdown[position + 1] if position + 1 < len(markdown) else " "
            if not (before.isalnum() and after.isalnum()):
                mask[position] = SYNTAX
        elif character == "[":
            mask[position] = SYNTAX   # a link's text is content; its brackets are not
    return mask


def words(markdown: str) -> list[Word]:
    """Every content word of `markdown`, in order, with its code point range."""
    mask = content_mask(markdown)
    found: list[Word] = []
    start = None
    characters: list[str] = []
    last_content = -1
    for position, kind in enumerate(mask):
        if kind == CONTENT:
            if start is None:
                start = position
            characters.append(markdown[position])
            last_content = position
        elif kind == BREAK and start is not None:
            found.append(Word(start, last_content + 1, "".join(characters)))
            start, characters = None, []
    if start is not None:
        found.append(Word(start, last_content + 1, "".join(characters)))
    return found


def normalize(text: str) -> str:
    """NFKC (which also expands ﬁ/ﬂ ligatures), invisible characters removed, dashes and quotes folded."""
    return unicodedata.normalize("NFKC", text).translate(_INVISIBLE).translate(_FOLDS)


def normalized_words(markdown: str) -> list[str]:
    """The comparison form of a document: its content words, normalised, empty ones dropped."""
    return [token for token in (normalize(word.text).strip() for word in words(markdown)) if token]
