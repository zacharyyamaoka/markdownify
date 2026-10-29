// document.md → a small render tree whose every text piece keeps its range in the markdown, so a click on any
// rendered word names the markdown word (index.json §3.2) it came from, and a word can be found again to light it.
//
// WHY @lezer/markdown (with its GFM tables, a pinned copy in vendor/): it hands back exact source positions for
// every node — a renderer that throws positions away (marked, markdown-it's HTML) would leave no way back from a
// rendered word to its range. Markdownify writes a table GFM cannot hold as an HTML <table> inside the markdown
// (CONTRACT §2); that one shape is tokenised here, because a DOM parse would lose the offsets the same way.

import { GFM, parser as baseParser } from "@lezer/markdown";

const parser = baseParser.configure(GFM);

/** Index ranges count code points (CONTRACT §3, `range_units`); the parser and the DOM count UTF-16 units. */
export function codePointToUtf16Map(text) {
  let astral = false;
  for (let index = 0; index < text.length; index += 1) {
    const unit = text.charCodeAt(index);
    if (unit >= 0xd800 && unit <= 0xdbff) {
      astral = true;
      break;
    }
  }
  if (!astral) return (codePoint) => codePoint;
  const offsets = [];
  let utf16 = 0;
  for (const character of text) {
    offsets.push(utf16);
    utf16 += character.length;
  }
  offsets.push(utf16);
  return (codePoint) => offsets[Math.min(codePoint, offsets.length - 1)];
}

// A node is either a piece — {from, to, text}: a run of rendered text and the markdown range (UTF-16) it came from,
// whose text differs from the raw slice only for an escape (`\~` → `~`) or a character reference (`&#126;` → `~`) —
// or an element — {tag, attrs?, cell?, children}, where `cell` is the index.json table_cell id a table cell holds.
export const isPiece = (node) => "text" in node;

let decoder = null;
const decoded = new Map();

/** A character reference as the browser decodes it (`&amp;` → `&`). */
function decodeEntity(raw) {
  const known = decoded.get(raw);
  if (known !== undefined) return known;
  decoder ??= document.createElement("textarea");
  decoder.innerHTML = raw;
  decoded.set(raw, decoder.value);
  return decoder.value;
}

/** Text with its character references decoded into their own pieces, so a word still splits at its exact range. */
function textPieces(source, from, to) {
  const pieces = [];
  const entity = /&(#\d+|#x[0-9a-fA-F]+|[a-zA-Z][a-zA-Z0-9]*);/g;
  const text = source.slice(from, to);
  let last = 0;
  for (let match = entity.exec(text); match; match = entity.exec(text)) {
    if (match.index > last) pieces.push({ from: from + last, to: from + match.index, text: text.slice(last, match.index) });
    pieces.push({ from: from + match.index, to: from + match.index + match[0].length, text: decodeEntity(match[0]) });
    last = match.index + match[0].length;
  }
  if (last < text.length) pieces.push({ from: from + last, to, text: text.slice(last) });
  return pieces;
}

const SKIPPED_INLINE = new Set([
  "EmphasisMark", "CodeMark", "LinkMark", "HeaderMark", "ListMark", "QuoteMark", "TableDelimiter",
  "StrikethroughMark", "TaskMarker", "URL", "LinkTitle", "LinkLabel", "Comment", "ProcessingInstruction",
]);

const INLINE_TAGS = {
  Emphasis: "em",
  StrongEmphasis: "strong",
  InlineCode: "code",
  Strikethrough: "del",
  Link: "span",
  Image: "span",
  Autolink: "span",
};

function inline(source, node, from = node.from, to = node.to) {
  const out = [];
  let cursor = from;
  const plain = (end) => {
    if (end > cursor) out.push(...textPieces(source, cursor, end));
  };
  for (let child = node.firstChild; child; child = child.nextSibling) {
    if (child.to <= from || child.from >= to) continue;
    plain(child.from);
    cursor = child.to;
    if (SKIPPED_INLINE.has(child.name)) continue;
    if (child.name === "Escape") {
      out.push({ from: child.from, to: child.to, text: source.slice(child.from + 1, child.to) });
    } else if (child.name === "Entity") {
      out.push({ from: child.from, to: child.to, text: decodeEntity(source.slice(child.from, child.to)) });
    } else if (child.name === "HardBreak") {
      out.push({ tag: "br", children: [] });
    } else if (child.name === "HTMLTag") {
      if (/^<br\s*\/?>$/i.test(source.slice(child.from, child.to))) out.push({ tag: "br", children: [] });
    } else if (INLINE_TAGS[child.name]) {
      out.push({ tag: INLINE_TAGS[child.name], children: inline(source, child) });
    } else {
      out.push(...inline(source, child));
    }
  }
  plain(to);
  return trimEdges(out);
}

/** A piece is "plain" when its text is the raw slice (the lengths agree); only a plain piece may be re-cut. */
const isPlain = (piece) => piece.to - piece.from === piece.text.length;

/** Leading/trailing whitespace of a block is layout, not text: drop it so cells and headings don't start with a space. */
function trimEdges(nodes) {
  const first = nodes[0];
  if (first && isPiece(first) && isPlain(first) && /^\s/.test(first.text)) {
    const trimmed = first.text.replace(/^\s+/, "");
    nodes[0] = { from: first.to - trimmed.length, to: first.to, text: trimmed };
  }
  const last = nodes[nodes.length - 1];
  if (last && isPiece(last) && isPlain(last) && /\s$/.test(last.text)) {
    const trimmed = last.text.replace(/\s+$/, "");
    nodes[nodes.length - 1] = { from: last.from, to: last.from + trimmed.length, text: trimmed };
  }
  return nodes.filter((node) => !isPiece(node) || node.text.length > 0);
}

function tableRow(source, row, cellTag) {
  // GFM rows: cells sit between `|` delimiters, and an empty cell has no TableCell node at all — so the cells are
  // the gaps between delimiters, each holding a TableCell or nothing.
  const delimiters = [];
  const cells = [];
  for (let child = row.firstChild; child; child = child.nextSibling) {
    if (child.name === "TableDelimiter") delimiters.push([child.from, child.to]);
    else if (child.name === "TableCell") cells.push(child);
  }
  const bounds = [];
  let start = row.from;
  for (const [from, to] of delimiters) {
    bounds.push({ start, end: from });
    start = to;
  }
  bounds.push({ start, end: row.to });
  const text = (range) => source.slice(range.start, range.end);
  if (bounds.length && !text(bounds[0]).trim() && delimiters[0]?.[0] === row.from) bounds.shift();
  if (bounds.length && !text(bounds[bounds.length - 1]).trim()) bounds.pop();
  return {
    tag: "tr",
    children: bounds.map((range) => {
      const cell = cells.find((candidate) => candidate.from >= range.start && candidate.to <= range.end);
      return { tag: cellTag, children: cell ? inline(source, cell) : [] };
    }),
  };
}

/** Markdownify's HTML <table> (merged cells): tags tokenised in place, text between them kept with its offsets. */
function htmlTable(source, from, to) {
  const tag = /<(\/?)([a-zA-Z][a-zA-Z0-9]*)([^>]*)>/g;
  tag.lastIndex = from;
  const root = { tag: "root", children: [] };
  const stack = [root];
  let cursor = from;
  const top = () => stack[stack.length - 1];
  for (let match = tag.exec(source); match && match.index < to; match = tag.exec(source)) {
    const inCell = ["td", "th"].includes(top().tag);
    if (inCell && match.index > cursor) top().children.push(...textPieces(source, cursor, match.index));
    cursor = match.index + match[0].length;
    const [, closing, rawName, rawAttrs] = match;
    const name = rawName.toLowerCase();
    if (name === "br") {
      if (inCell) top().children.push({ tag: "br", children: [] });
      continue;
    }
    if (!["table", "thead", "tbody", "tr", "td", "th"].includes(name)) continue;
    if (closing) {
      const at = stack.map((element) => element.tag).lastIndexOf(name);
      if (at > 0) stack.length = at;
      continue;
    }
    const attrs = {};
    for (const attr of rawAttrs.matchAll(/(rowspan|colspan)\s*=\s*"?(\d+)"?/gi)) attrs[attr[1].toLowerCase()] = Number(attr[2]);
    const element = { tag: name, attrs, children: [] };
    top().children.push(element);
    stack.push(element);
  }
  for (const element of root.children) if (!isPiece(element)) trimCells(element);
  return root.children.filter((node) => !isPiece(node));
}

function trimCells(element) {
  if (element.tag === "td" || element.tag === "th") {
    element.children = trimEdges(element.children);
    return;
  }
  for (const child of element.children) if (!isPiece(child)) trimCells(child);
}

function block(source, node) {
  switch (node.name) {
    case "Paragraph":
      return [{ tag: "p", children: inline(source, node) }];
    case "ATXHeading1": case "ATXHeading2": case "ATXHeading3":
    case "ATXHeading4": case "ATXHeading5": case "ATXHeading6":
    case "SetextHeading1": case "SetextHeading2":
      return [{ tag: `h${node.name.slice(-1)}`, children: inline(source, node) }];
    case "BulletList":
    case "OrderedList":
      return [{ tag: node.name === "BulletList" ? "ul" : "ol", children: children(source, node) }];
    case "ListItem": {
      const items = children(source, node);
      // A tight list item's single paragraph renders as the item itself, as GFM does.
      const only = items.length === 1 && !isPiece(items[0]) && items[0].tag === "p" ? items[0].children : items;
      return [{ tag: "li", children: only }];
    }
    case "Blockquote":
      return [{ tag: "blockquote", children: children(source, node) }];
    case "FencedCode":
    case "CodeBlock": {
      const texts = [];
      for (let child = node.firstChild; child; child = child.nextSibling) {
        if (child.name === "CodeText") texts.push({ from: child.from, to: child.to, text: source.slice(child.from, child.to) });
      }
      return [{ tag: "pre", children: texts }];
    }
    case "HorizontalRule":
      return [{ tag: "hr", children: [] }];
    case "Table": {
      const head = [];
      const body = [];
      for (let child = node.firstChild; child; child = child.nextSibling) {
        if (child.name === "TableHeader") head.push(tableRow(source, child, "th"));
        else if (child.name === "TableRow") body.push(tableRow(source, child, "td"));
      }
      return [{ tag: "table", children: [{ tag: "thead", children: head }, { tag: "tbody", children: body }] }];
    }
    case "HTMLBlock":
      if (/^\s*<table/i.test(source.slice(node.from, node.to))) return htmlTable(source, node.from, node.to);
      return [{ tag: "pre", children: textPieces(source, node.from, node.to) }];
    case "LinkReference":
    case "CommentBlock":
    case "ProcessingInstructionBlock":
      return [];
    default:
      return [{ tag: "div", children: inline(source, node) }];
  }
}

function children(source, node) {
  const out = [];
  for (let child = node.firstChild; child; child = child.nextSibling) {
    if (child.name === "ListMark" || child.name === "QuoteMark") continue;
    out.push(...block(source, child));
  }
  return out;
}

/** The render tree of a whole document.md. */
export function parseMarkdown(source) {
  const tree = parser.parse(source);
  // WHY an HTML <table> may span several HTMLBlocks: a blank line inside it ends the block; one table per block
  // is what Markdownify writes today, and a split one still renders, cell by cell.
  return children(source, tree.topNode);
}

/** Words in UTF-16 units, sorted, each carrying its position in the merged word list (index words, then unlinked). */
export function linkedWords(words, toUtf16) {
  return words
    .map((word, index) => ({ from: toUtf16(word.range.start), to: toUtf16(word.range.end), index }))
    .filter((word) => word.to > word.from)
    .sort((a, b) => a.from - b.from);
}

/** Cut each plain piece at word boundaries; a decoded piece (escape, entity) goes whole to the word it overlaps. */
export function wordRuns(piece, words) {
  const runs = [];
  // First word that ends after the piece starts.
  let low = 0;
  let high = words.length;
  while (low < high) {
    const middle = (low + high) >> 1;
    if (words[middle].to <= piece.from) low = middle + 1;
    else high = middle;
  }
  if (!isPlain(piece)) {
    const word = low < words.length && words[low].from < piece.to ? words[low].index : -1;
    return [{ ...piece, word }];
  }
  let cursor = piece.from;
  for (let at = low; at < words.length && words[at].from < piece.to; at += 1) {
    const start = Math.max(words[at].from, piece.from);
    const end = Math.min(words[at].to, piece.to);
    if (start > cursor) runs.push({ from: cursor, to: start, text: piece.text.slice(cursor - piece.from, start - piece.from), word: -1 });
    runs.push({ from: start, to: end, text: piece.text.slice(start - piece.from, end - piece.from), word: words[at].index });
    cursor = end;
  }
  if (cursor < piece.to) runs.push({ from: cursor, to: piece.to, text: piece.text.slice(cursor - piece.from), word: -1 });
  return runs;
}

/** index.json's table cells in UTF-16 units, sorted by start. */
export function cellRanges(blocks, toUtf16) {
  return blocks
    .filter((block) => block.type === "table_cell")
    .map((block) => ({ id: block.id, from: toUtf16(block.range.start), to: toUtf16(block.range.end) }))
    .sort((a, b) => a.from - b.from);
}

function flatten(node) {
  return isPiece(node) ? [node] : node.children.flatMap(flatten);
}

/** Attach to each rendered table cell the index.json table_cell whose range it holds (both in UTF-16). */
export function attachCells(nodes, cells) {
  const visit = (node) => {
    if (isPiece(node)) return;
    if (node.tag === "td" || node.tag === "th") {
      const pieces = flatten(node);
      if (pieces.length) {
        const from = pieces[0].from;
        const to = pieces[pieces.length - 1].to;
        node.cell = cells.find((cell) => cell.from < to && cell.to > from)?.id;
      }
      return;
    }
    node.children.forEach(visit);
  };
  nodes.forEach(visit);
}

// ---- the DOM ---------------------------------------------------------------------------------------------------

function appendRuns(parent, piece, words, unlinked) {
  for (const run of wordRuns(piece, words)) {
    if (run.word < 0) {
      parent.append(run.text);
      continue;
    }
    const span = document.createElement("span");
    span.className = "md-word";
    span.dataset.w = String(run.word);
    if (unlinked.has(run.word)) span.dataset.unlinked = "";
    span.textContent = run.text;
    parent.append(span);
  }
}

function appendNodes(parent, nodes, words, unlinked) {
  for (const node of nodes) {
    if (isPiece(node)) {
      appendRuns(parent, node, words, unlinked);
      continue;
    }
    const element = document.createElement(node.tag);
    for (const [name, value] of Object.entries(node.attrs ?? {})) element.setAttribute(name, String(value));
    if (node.cell) element.dataset.cell = node.cell;
    appendNodes(element, node.children, words, unlinked);
    parent.append(element);
  }
}

/** The rendered document: a fragment of real elements, every word a span carrying its `data-w`. */
export function renderDocument(nodes, words, unlinked) {
  const fragment = document.createDocumentFragment();
  appendNodes(fragment, nodes, words, unlinked);
  return fragment;
}

/** document.md verbatim in one <pre>, with the same word spans. */
export function renderRaw(markdown, words, unlinked) {
  const pre = document.createElement("pre");
  pre.className = "md-raw";
  appendRuns(pre, { from: 0, to: markdown.length, text: markdown }, words, unlinked);
  return pre;
}
