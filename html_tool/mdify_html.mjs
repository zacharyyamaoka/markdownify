#!/usr/bin/env node
/**
 * Markdownify pipeline A for web pages (CONTRACT.md §3.3). Reads `{"html", "url"}` as JSON on stdin — `html` is the
 * page already decoded to a string by `mdify.html`, so both sides count offsets in the same string — and writes one
 * JSON object to stdout.
 *
 *   node mdify_html.mjs convert [--reader]   the markdown, its blocks, the inventory (+ alt and svg tooltip
 *                                            words) and what was left out, with why
 *
 * Default (conservative) mode: Defuddle writes and standardises the markdown of the whole <body>; only removals
 * with a known reason are made (`conservativeRemovals`). `--reader`: Defuddle also picks the content, as in
 * Obsidian's reading view. WHY the default: see `convert`.
 *   node mdify_html.mjs inventory   the inventory alone; never loads Defuddle
 *
 * WHY two libraries: Defuddle (kepano, MIT) is the best open judge of "what on this page is content" and writes
 * the markdown Obsidian's Web Clipper writes, but it clones the DOM and serialises it, so nothing it returns points
 * back at the page. jsdom's `includeNodeLocations` knows where every element and text node sat in the HTML string.
 * The seam between them is an id stamped on every element before Defuddle runs (`data-mdify-id`, let through
 * Defuddle's attribute allow-list): an id that survives is content, a subtree whose ids all vanished was removed.
 *
 * WHY the allow-list and not `debug: true`: debug mode also keeps `data-*`, but it changes how Defuddle flattens
 * wrapper divs, so the markdown would no longer be the markdown Defuddle writes for everyone else.
 *
 * Block ranges in the markdown come from private-use sentinels written into the text of each block of Defuddle's
 * cleaned HTML just before its own Turndown step runs; they are stripped afterwards. The markdown handed back is
 * always Defuddle's own (`reference_markdown`); when the sentinels changed it (a line-start escape Turndown no
 * longer saw), `marked_markdown` is the sentinel run with them stripped and `mdify.html` maps its ranges across.
 *
 * Defuddle reads a linkedom re-parse of the stamped page (see `convert`); jsdom keeps the locations.
 *
 * Not a browser, for v0: Defuddle skips `getComputedStyle` in Node and loads no external stylesheet, so a
 * menu hidden only by CSS still reaches its selector and link-density checks, and the inventory below counts it as
 * visible. A headless Chromium pass would add those checks; the selectors already catch CubeMars' menus.
 *
 * Offsets: everything leaves this file in Unicode code points (CONTRACT §3, `range_units`); JavaScript strings
 * count UTF-16 units, so each offset goes through `codePointIndex` on the way out.
 */
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const { JSDOM, VirtualConsole } = require('jsdom');
const { decodeHTML } = require('entities');

// WHY: jsdom and Defuddle can log (CSS parse errors, debug notes); stdout carries the JSON and nothing else.
console.log = console.info = console.warn = console.debug = (...args) => process.stderr.write(args.join(' ') + '\n');

const STAMP = 'data-mdify-id';

// Never rendered as page text: the document head, code, templates, embedded documents, and the parts of an svg
// that are not drawn (its <title>/<desc> tooltips, definitions). An svg's <text> IS drawn: a chart's axis labels are
// visible words, so they are in the inventory (and land as flagged, inside the svg's represented:false block).
const INVISIBLE_TAGS = new Set(['head', 'title', 'script', 'style', 'noscript', 'template', 'iframe', 'object',
  'embed', 'canvas', 'video', 'audio', 'datalist', 'desc', 'defs', 'symbol', 'metadata', 'clipPath', 'mask',
  'marker', 'pattern', 'linearGradient', 'radialGradient']);
// Elements that flow inside a line: a word may run across them ("<b>20</b>Nm" is one word on screen). Every other
// element starts and ends a box, so a word never joins across it.
const INLINE_TAGS = new Set(['a', 'abbr', 'b', 'bdi', 'bdo', 'big', 'cite', 'code', 'data', 'del', 'dfn', 'em',
  'font', 'i', 'ins', 'kbd', 'label', 'mark', 'nobr', 'q', 's', 'samp', 'small', 'span', 'strike', 'strong', 'sub',
  'sup', 'time', 'tt', 'u', 'var', 'wbr']);
// WHY display:none only, not visibility:hidden: scroll-reveal libraries (WOW.js, on ti5robot and mdmbot) ship
// content as `visibility: hidden` and reveal it on scroll, so on screen it is read like any other text.
const INLINE_STYLE_HIDDEN = /(^|;)\s*display\s*:\s*none\s*(!important)?\s*(;|$)/i;
const ENTITY = /&(?:#[xX][0-9a-fA-F]+;?|#[0-9]+;?|[A-Za-z][A-Za-z0-9]*;?)/y;
const WHITESPACE = /\s/;

// Markdown block types (CONTRACT §3.1) for the elements of Defuddle's cleaned HTML.
const BLOCK_TYPES = {
  h1: 'heading', h2: 'heading', h3: 'heading', h4: 'heading', h5: 'heading', h6: 'heading',
  p: 'paragraph', dt: 'paragraph', dd: 'paragraph', li: 'list_item', pre: 'code',
  table: 'table', td: 'table_cell', th: 'table_cell', caption: 'caption', figcaption: 'caption',
  // WHY figure, represented false: Defuddle keeps an inline svg as raw HTML, which is not a conversion of what it
  // draws; whether it is a plot or a diagram is not known from markup, so it is the general kind.
  svg: 'figure',
};

// Why Defuddle dropped a subtree, read from the subtree's own markup (nearest element first). The reasons are
// CONTRACT §5's; an <aside> is `boilerplate` with detail "aside"; no telling markup is reason null.
// WHY markup and not Defuddle's removal log: the log exists only in debug mode, which changes the output.
const NAVIGATION_TAGS = new Set(['nav', 'menu']);
const NAVIGATION_ROLES = new Set(['navigation', 'menu', 'menubar', 'menuitem', 'tablist']);
// Class/id names: a whole token, optionally with a site/mobile prefix ("mheader", "site-footer", "nav_pc").
const NAME = (words) => new RegExp(`(^|[\\s_-])(m|mobile|pc|site|page|global|top|main)?[_-]?(${words})([\\s_-]|$)`, 'i');
const NAVIGATION_NAME = NAME('nav|navbar|navigation|menu|menus|megamenu|mega-menu|submenu|dropdown|breadcrumbs?');
const HEADER_NAME = NAME('header|masthead|topbar|top-bar');
const FOOTER_NAME = NAME('footer|copyright');
const ASIDE_NAME = NAME('sidebar|aside');
const TABLE_PARTS = new Set(['table', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td', 'caption', 'colgroup', 'col']);

const SENTINEL_START_OPEN = '', SENTINEL_START_CLOSE = '';
const SENTINEL_END_OPEN = '', SENTINEL_END_CLOSE = '';
const SENTINEL_DIGIT_ZERO = 0xE010;

function codePointIndex(text) {
  // For every UTF-16 index (and the end), the code-point index it starts at.
  const index = new Uint32Array(text.length + 1);
  let codePoint = 0;
  for (let unit = 0; unit < text.length; unit++) {
    index[unit] = codePoint;
    const code = text.charCodeAt(unit);
    if (code >= 0xD800 && code <= 0xDBFF && unit + 1 < text.length) {
      const next = text.charCodeAt(unit + 1);
      if (next >= 0xDC00 && next <= 0xDFFF) index[++unit] = codePoint;
    }
    codePoint++;
  }
  index[text.length] = codePoint;
  return index;
}

class Page {
  /** The fetched page parsed once, with every element numbered in document order and located in the HTML. */
  constructor(html, url) {
    this.html = html;
    this.dom = new JSDOM(html, { includeNodeLocations: true, url: url || undefined, virtualConsole: new VirtualConsole() });
    this.document = this.dom.window.document;
    this.codePoint = codePointIndex(html);
    this.elements = Array.from(this.document.querySelectorAll('*'));
    this.number = new Map(this.elements.map((element, index) => [element, index]));
    this.spans = new Map();
    this.xpaths = this.#xpaths();
    this.mappingFallbacks = 0;
  }

  #xpaths() {
    // One pass: a step carries [n] only when a sibling shares its name, as browsers' "Copy XPath" writes it.
    const xpaths = new Map();
    const visit = (parent, prefix) => {
      const counts = new Map();
      for (const child of parent.children) counts.set(child.localName, (counts.get(child.localName) || 0) + 1);
      const seen = new Map();
      for (const child of parent.children) {
        const position = (seen.get(child.localName) || 0) + 1;
        seen.set(child.localName, position);
        const step = counts.get(child.localName) > 1 ? `${child.localName}[${position}]` : child.localName;
        xpaths.set(child, `${prefix}/${step}`);
      }
    };
    visit(this.document, '');
    for (const element of this.elements) visit(element, xpaths.get(element));
    return xpaths;
  }

  span(node) {
    // [start, end) in UTF-16 units of the HTML. Elements the parser implied (a <tbody>, an omitted <head>) have no
    // location of their own and take the extent of their children.
    if (this.spans.has(node)) return this.spans.get(node);
    const location = this.dom.nodeLocation(node);
    let result = location ? [location.startOffset, location.endOffset] : null;
    if (!result && node.nodeType === 1) {
      let start = Infinity, end = -Infinity;
      for (const child of node.childNodes) {
        const childSpan = this.span(child);
        if (childSpan) { start = Math.min(start, childSpan[0]); end = Math.max(end, childSpan[1]); }
      }
      result = start < end ? [start, end] : null;
    }
    this.spans.set(node, result);
    return result;
  }

  range(span) {
    return span ? { start: this.codePoint[span[0]], end: this.codePoint[span[1]] } : null;
  }

  hidden(element) {
    const tag = element.localName;
    if (INVISIBLE_TAGS.has(tag)) return true;
    if (element.hasAttribute('hidden')) return true;
    if (tag === 'input' && (element.getAttribute('type') || '').toLowerCase() === 'hidden') return true;
    return INLINE_STYLE_HIDDEN.test(element.getAttribute('style') || '');
  }

  textUnits(node) {
    // For each UTF-16 unit of the text node's decoded data, the raw [start, end) it was written as in the HTML:
    // "&nbsp;" is one character with a six-unit source, "\r\n" one "\n".
    const span = this.span(node);
    return span ? this.rawUnits(span[0], span[1], node.data) : null;
  }

  rawUnits(base, limit, data) {
    // Walks html[base, limit) decoding character references the way the tokenizer did, until it has produced
    // `data` (what the parser stored) unit by unit.
    const starts = new Uint32Array(data.length), ends = new Uint32Array(data.length);
    let decoded = '', raw = base;
    const push = (text, start, end) => {
      for (let unit = 0; unit < text.length && decoded.length < data.length; unit++) {
        starts[decoded.length] = start; ends[decoded.length] = end; decoded += text[unit];
      }
    };
    while (raw < limit && decoded.length < data.length) {
      const char = this.html[raw];
      if (char === '&') {
        ENTITY.lastIndex = raw;
        const match = ENTITY.exec(this.html);
        if (match && match.index === raw && raw + match[0].length <= limit) {
          const text = decodeHTML(match[0]);
          if (text !== match[0]) { push(text, raw, raw + match[0].length); raw += match[0].length; continue; }
        }
      }
      if (char === '\r') {
        const width = this.html[raw + 1] === '\n' ? 2 : 1;
        push('\n', raw, raw + width); raw += width; continue;
      }
      push(char, raw, raw + 1); raw++;
    }
    if (decoded !== data) {
      // The parser changed the text in a way this walk does not model (a NUL, a foster-parented run). Every
      // character then points at the whole run: coarse but never wrong about which node it came from.
      this.mappingFallbacks++;
      starts.fill(base); ends.fill(limit);
    }
    return { starts, ends };
  }

  extraWords() {
    /** Words Defuddle can write that are not visible page text, located exactly: an svg's tooltip, and an image's
     * alt text inside the attribute value — Defuddle writes alt as a caption paragraph when the image has none of
     * its own. Not
     * part of the inventory, so the ledger's source side never counts them; they only give those markdown words
     * a place to point. */
    const words = [];
    const shown = (element) => {
      for (let up = element.parentElement; up; up = up.parentElement) {
        if (up.localName !== 'title' && up.localName !== 'desc' && this.hidden(up)) return false;
      }
      return true;
    };
    // An svg's <title>/<desc> is a tooltip, not drawn text — but Defuddle keeps it inside the raw svg it writes.
    for (const tooltip of this.document.querySelectorAll('svg title, svg desc')) {
      if (!shown(tooltip)) continue;
      const box = this.number.get(tooltip);
      for (const node of tooltip.childNodes) {
        if (node.nodeType !== 3 || !node.data.trim()) continue;
        const units = this.textUnits(node);
        if (!units) continue;
        for (const match of node.data.matchAll(/\S+/g)) {
          const first = match.index, last = match.index + match[0].length - 1;
          words.push({ text: match[0], source_range: this.range([units.starts[first], units.ends[last]]),
            xpath: this.xpaths.get(tooltip), element: box, box, attribute: null });
        }
      }
    }
    for (const image of this.document.querySelectorAll('img[alt]')) {
      if (!shown(image) || this.hidden(image)) continue;
      const location = this.dom.nodeLocation(image);
      const attribute = location && location.attrs && location.attrs.alt;
      const value = image.getAttribute('alt');
      if (!attribute || !value.trim()) continue;
      const written = this.html.slice(attribute.startOffset, attribute.endOffset);
      let at = written.indexOf('=') + 1;
      if (at <= 0) continue;
      while (at < written.length && WHITESPACE.test(written[at])) at++;
      const quoted = written[at] === '"' || written[at] === "'";
      const base = attribute.startOffset + at + (quoted ? 1 : 0);
      const limit = attribute.endOffset - (quoted ? 1 : 0);
      const units = this.rawUnits(base, limit, value);
      const xpath = `${this.xpaths.get(image)}/@alt`, number = this.number.get(image);
      for (const match of value.matchAll(/\S+/g)) {
        const first = match.index, last = match.index + match[0].length - 1;
        words.push({ text: match[0], source_range: this.range([units.starts[first], units.ends[last]]), xpath,
          element: number, box: number, attribute: 'alt' });
      }
    }
    return words;
  }

  boxOf(element) {
    // The nearest ancestor-or-self that is not inline: the box a word is read in, for text-quote context.
    for (let current = element; current; current = current.parentElement) {
      if (!INLINE_TAGS.has(current.localName)) return current;
    }
    return element;
  }

  inventory() {
    /** The page's visible words in reading order, each with where it was written in the HTML. Independent of
     * Defuddle: this is the source side of the ledger (CONTRACT §5). */
    const words = [];
    let current = null;
    const flush = () => {
      if (!current) return;
      const element = current.firstNode.parentElement;
      let common = element;
      if (current.lastNode !== current.firstNode) {
        while (common && !common.contains(current.lastNode)) common = common.parentElement;
      }
      words.push({
        text: current.text,
        source_range: this.range([current.start, current.end]),
        xpath: this.xpaths.get(common || element),
        element: this.number.get(element),
        box: this.number.get(this.boxOf(element)),
      });
      current = null;
    };
    const body = this.document.body || this.document.documentElement;
    const stack = [[body, false]];
    while (stack.length) {
      const [node, leaving] = stack.pop();
      if (node.nodeType === 3) {
        if (!node.data.trim()) continue;   // whitespace only: no words, nothing to locate
        const units = this.textUnits(node);
        if (!units) continue;
        for (let unit = 0; unit < node.data.length; unit++) {
          const char = node.data[unit];
          if (WHITESPACE.test(char)) { flush(); continue; }
          if (!current) current = { text: '', start: units.starts[unit], firstNode: node };
          current.text += char;
          current.end = units.ends[unit];
          current.lastNode = node;
        }
        continue;
      }
      if (node.nodeType !== 1) continue;
      const inline = INLINE_TAGS.has(node.localName);
      if (leaving) { if (!inline) flush(); continue; }
      if (this.hidden(node)) continue;
      if (!inline) flush();
      stack.push([node, true]);
      for (let index = node.childNodes.length - 1; index >= 0; index--) stack.push([node.childNodes[index], false]);
    }
    flush();
    return words;
  }

  dropReason(element) {
    for (let current = element; current && current.nodeType === 1; current = current.parentElement) {
      const tag = current.localName;
      const role = (current.getAttribute('role') || '').trim().toLowerCase();
      const name = `${current.getAttribute('class') || ''} ${current.getAttribute('id') || ''}`;
      if (NAVIGATION_TAGS.has(tag) || NAVIGATION_ROLES.has(role)) return { reason: 'navigation', detail: tag === 'nav' || tag === 'menu' ? `<${tag}>` : `role=${role}` };
      if (tag === 'header' || role === 'banner') return { reason: 'header', detail: tag === 'header' ? '<header>' : 'role=banner' };
      if (tag === 'footer' || role === 'contentinfo') return { reason: 'footer', detail: tag === 'footer' ? '<footer>' : 'role=contentinfo' };
      if (tag === 'aside' || role === 'complementary') return { reason: 'boilerplate', detail: 'aside' };
      if (tag === 'body' || tag === 'html') break;
      // WHY: a table's own parts are never page chrome, whatever their class says (pandoc writes
      // <tr class="header"> for a header row, and Defuddle's partial selector removes it as a page header).
      if (TABLE_PARTS.has(tag)) continue;
      if (NAVIGATION_NAME.test(name)) return { reason: 'navigation', detail: `.${name.trim().split(/\s+/)[0]}` };
      if (HEADER_NAME.test(name)) return { reason: 'header', detail: `.${name.trim().split(/\s+/)[0]}` };
      if (FOOTER_NAME.test(name)) return { reason: 'footer', detail: `.${name.trim().split(/\s+/)[0]}` };
      if (ASIDE_NAME.test(name)) return { reason: 'boilerplate', detail: 'aside' };
    }
    // No markup says why: Defuddle judged it not content. Left without a reason, so `mdify.html` does not count
    // it as dropped — when Defuddle's pick was too narrow these are real content words and must show as lost.
    return { reason: null, detail: `not kept by Defuddle (<${element.localName}>)` };
  }
}

function sentinelDigits(number) {
  return String(number).split('').map((digit) => String.fromCharCode(SENTINEL_DIGIT_ZERO + Number(digit))).join('');
}

function stripSentinels(marked) {
  // Defuddle's markdown with the sentinels taken out, and each block's [start, end) in UTF-16 units of the result.
  let out = '';
  const starts = new Map(), ends = new Map();
  for (let unit = 0; unit < marked.length; unit++) {
    const char = marked[unit];
    if (char === SENTINEL_START_OPEN || char === SENTINEL_END_OPEN) {
      const close = char === SENTINEL_START_OPEN ? SENTINEL_START_CLOSE : SENTINEL_END_CLOSE;
      let digits = '', cursor = unit + 1;
      while (cursor < marked.length && marked[cursor] !== close) {
        digits += String(marked.charCodeAt(cursor) - SENTINEL_DIGIT_ZERO);
        cursor++;
      }
      (char === SENTINEL_START_OPEN ? starts : ends).set(Number(digits), out.length);
      unit = cursor;
      continue;
    }
    out += char;
  }
  return { markdown: out, starts, ends };
}

function tableCells(table, original) {
  // Row/col of each cell on the table's grid, honouring rowspan and colspan, and its header kind (CONTRACT §3.1).
  // The grid is read from Defuddle's table (that is what the markdown shows); `scope` from the page's own cell,
  // because Defuddle's attribute allow-list strips it.
  const cells = new Map();
  const taken = [];
  const rows = Array.from(table.rows || []);
  const rowHasData = rows.map((row) => Array.from(row.cells).some((cell) => cell.localName === 'td'));
  rows.forEach((row, rowIndex) => {
    taken[rowIndex] = taken[rowIndex] || [];
    let col = 0;
    for (const cell of row.cells) {
      while (taken[rowIndex][col]) col++;
      const colspan = Math.max(1, Math.min(1000, parseInt(cell.getAttribute('colspan') || '1', 10) || 1));
      let rowspan = parseInt(cell.getAttribute('rowspan') || '1', 10);
      if (rowspan === 0) rowspan = rows.length - rowIndex;
      rowspan = Math.max(1, Math.min(rows.length - rowIndex, rowspan || 1));
      for (let r = rowIndex; r < rowIndex + rowspan; r++) {
        taken[r] = taken[r] || [];
        for (let c = col; c < col + colspan; c++) taken[r][c] = true;
      }
      const source = original(cell);
      const scope = ((source || cell).getAttribute('scope') || '').toLowerCase();
      let kind = 'content';
      if (cell.localName === 'th') {
        if (scope === 'col' || scope === 'colgroup') kind = 'columnHeader';
        else if (scope === 'row' || scope === 'rowgroup') kind = 'rowHeader';
        else if (row.parentElement && row.parentElement.localName === 'thead') kind = 'columnHeader';
        else kind = rowHasData[rowIndex] ? 'rowHeader' : 'columnHeader';
      }
      cells.set(cell, { row: rowIndex, col, rowspan, colspan, kind });
      col += colspan;
    }
  });
  const hasRowHeader = Array.from(cells.values()).some((cell) => cell.kind === 'rowHeader');
  for (const cell of cells.values()) {
    if (hasRowHeader && cell.row === 0 && cell.col === 0 && cell.kind === 'columnHeader') cell.kind = 'stubHead';
  }
  return cells;
}

// --- conservative content mode (the default) ---------------------------------------------------------------------
// Landmarks that are page chrome by definition (HTML-AAM): <nav>, <aside>, and <header>/<footer> when they belong
// to the page rather than to an article or section (an <article>'s <header> holds its title).
const SECTIONING = new Set(['article', 'aside', 'main', 'nav', 'section']);
const CHROME_ROLES = { navigation: 'navigation', menu: 'navigation', menubar: 'navigation', banner: 'header',
  contentinfo: 'footer', complementary: 'boilerplate' };
const DUPLICATE_MIN_WORDS = 20;      // a repeated block this long is a second rendering, not a coincidence
const MENU_COPY_MIN_WORDS = 3;

function squashText(element) {
  return (element.textContent || '').split(/\s+/).filter(Boolean).join(' ');
}

function linkDensity(element) {
  const total = squashText(element).length;
  if (!total) return 0;
  let linked = 0;
  for (const link of element.querySelectorAll('a[href]')) linked += squashText(link).length;
  return linked / total;
}

function chromeReason(element) {
  const tag = element.localName;
  const role = (element.getAttribute('role') || '').trim().toLowerCase();
  if (CHROME_ROLES[role]) return { reason: CHROME_ROLES[role], detail: role === 'complementary' ? 'aside' : `role=${role}` };
  if (tag === 'nav' || tag === 'menu') return { reason: 'navigation', detail: `<${tag}>` };
  if (tag === 'aside') return { reason: 'boilerplate', detail: 'aside' };
  if (tag === 'header' || tag === 'footer') {
    for (let up = element.parentElement; up; up = up.parentElement) if (SECTIONING.has(up.localName)) return null;
    return { reason: tag, detail: `<${tag}>` };
  }
  return null;
}

function conservativeRemovals(page) {
  /** What the default mode leaves out, decided here and not by Defuddle: element number -> {reason, detail}.
   * Only removals with a known reason: page landmarks (above), elements hidden in markup (`hidden`, inline
   * display:none — the inventory's own rule, so their words were never source words), and second copies (a
   * menu repeated in a mobile drawer, a block rendered twice). Script/style/template/noscript are Defuddle's
   * to drop: they hold no visible words. Everything else is kept — tab panels and short-item lists included. */
  const removals = new Map();
  const body = page.document.body || page.document.documentElement;
  const visit = (element, decide) => {
    const stack = [element];
    while (stack.length) {
      const current = stack.pop();
      if (removals.has(page.number.get(current))) continue;
      if (decide(current)) continue;
      for (let index = current.children.length - 1; index >= 0; index--) stack.push(current.children[index]);
    }
  };
  visit(body, (element) => {
    const number = page.number.get(element);
    if (element !== body && page.hidden(element) && !INVISIBLE_TAGS.has(element.localName)) {
      removals.set(number, { reason: null, detail: 'hidden in markup', silent: true });
      return true;
    }
    const chrome = element === body ? null : chromeReason(element);
    if (chrome) { removals.set(number, chrome); return true; }
    return false;
  });
  // Copies: of the chrome just found (link-dense, every word inside a chrome region's text), or of a long block
  // already seen earlier in the page.
  const chromeText = ' ' + [...removals.entries()].filter(([, removal]) => !removal.silent)
    .map(([number]) => squashText(page.elements[number])).join(' \n ') + ' ';
  const seen = new Map();
  visit(body, (element) => {
    if (element === body || TABLE_PARTS.has(element.localName) || INVISIBLE_TAGS.has(element.localName)) return false;
    const text = squashText(element);
    const words = text ? text.split(' ').length : 0;
    if (words >= MENU_COPY_MIN_WORDS && chromeText.includes(` ${text} `) && linkDensity(element) >= 0.5) {
      removals.set(page.number.get(element), { reason: 'duplicate', detail: 'copy of the page navigation' });
      return true;
    }
    if (words >= DUPLICATE_MIN_WORDS) {
      const first = seen.get(text);
      if (first && !first.contains(element)) {
        removals.set(page.number.get(element), { reason: 'duplicate', detail: `copy of ${page.xpaths.get(first)}` });
        return true;
      }
      if (!first) seen.set(text, element);
    }
    return false;
  });
  return removals;
}

async function convert(page, url, reader) {
  const defuddleNode = fileURLToPath(import.meta.resolve('defuddle/node'));
  const defuddleDist = path.dirname(defuddleNode);
  // Same module instances Defuddle itself loads, so the allow-list it reads is the one patched here.
  require(path.join(defuddleDist, 'constants.js')).ALLOWED_ATTRIBUTES.add(STAMP);
  const { createMarkdownContent } = require(path.join(defuddleDist, 'markdown.js'));
  const { parseLinkedomHTML } = require(path.join(defuddleDist, 'utils', 'linkedom-compat.js'));
  const { Defuddle } = require(defuddleNode);

  const inventory = page.inventory();       // before the stamps go on
  page.elements.forEach((element, index) => element.setAttribute(STAMP, String(index)));
  // WHY linkedom for Defuddle: it is Defuddle's own Node DOM (jsdom is only its dev dependency). jsdom 30's selector
  // engine refuses selectors over 2048 characters, and Defuddle's exact-selector list is one longer selector, so on
  // jsdom the whole selector step throws and CubeMars' catalogue menu survives as content. The stamped ids carry
  // jsdom's locations across the re-parse.
  // WHY useAsync false: no third-party fetches — a conversion reads only the cached bytes.
  const stampedDocument = parseLinkedomHTML(page.dom.serialize(), url || undefined);
  // WHY: a table's own parts are never page chrome, but Defuddle's partial selectors read their class as if they
  // were: pandoc's <tr class="header"> header row is removed as a page header, and with it every column header a
  // value's meaning depends on. Their classes only style the table, so they go before Defuddle looks.
  for (const part of stampedDocument.querySelectorAll('table, thead, tbody, tfoot, tr, th, td')) part.removeAttribute('class');
  // WHY two modes (coordinator, 2026-10-29, from this converter's ledger on robotparts' 46 cached maker pages):
  // Defuddle is built for reading mode. Left to pick the content it kept 0.51 of the visible words — it took
  // CubeMars' description <article> and lost the Specification tab, and its content patterns deleted short spec
  // lists and mdmbot's rated/peak torque line. Losing a spec table is data loss; keeping some boilerplate is cheap.
  // So by default Defuddle only writes the markdown and standardises it: the content root is <body>, its selector,
  // scoring, pattern and hidden-style removals are off, and `conservativeRemovals` drops only what has a known
  // reason. WHY its hidden check off too: it removes visibility:hidden and opacity:0, which scroll-reveal scripts
  // ship real content as. `--reader` gives Defuddle back its own judgement.
  const removals = reader ? new Map() : conservativeRemovals(page);
  if (removals.size) {
    const stamped = new Map();
    for (const element of stampedDocument.querySelectorAll(`[${STAMP}]`)) stamped.set(Number(element.getAttribute(STAMP)), element);
    for (const number of removals.keys()) stamped.get(number)?.remove();
  }
  if (!reader) {
    // WHY: Defuddle's standardisation deletes the first heading when it repeats the page title, because its reading
    // view prints the title above the article. This markdown has no title line, so that heading is the product's
    // name (the harness lost every case's <h1> to it). Defuddle reads the title once, through this extractor;
    // handing it an empty one leaves the heading in place.
    const { MetadataExtractor } = require(path.join(defuddleDist, 'metadata.js'));
    const extract = MetadataExtractor.extract.bind(MetadataExtractor);
    MetadataExtractor.extract = (...args) => ({ ...extract(...args), title: '' });
    // WHY: Defuddle's footnote standardisation guesses which paragraphs are footnote bodies from their position.
    // On a datasheet's "<sup>1</sup> Measured at 25 °C…" notes it renumbered them and tied the body's [^1] to the
    // second note (harness case 07: the wrong test condition on peak torque), dropping the labels. A wrong
    // condition on a value is worse than a footnote left as written, so the notes stay as the page wrote them.
    require(path.join(defuddleDist, 'elements', 'footnotes.js')).standardizeFootnotes = () => {};
  }
  const options = reader ? { useAsync: false } : { useAsync: false, contentSelector: 'body', removeExactSelectors: false,
    removePartialSelectors: false, removeLowScoring: false, removeContentPatterns: false, removeHiddenElements: false };
  const result = await Defuddle(stampedDocument, url || 'about:blank', options);

  const host = new JSDOM('', { virtualConsole: new VirtualConsole() }).window.document;
  const content = host.createElement('div');
  content.innerHTML = result.content || '';
  const kept = new Set();
  for (const element of content.querySelectorAll(`[${STAMP}]`)) kept.add(Number(element.getAttribute(STAMP)));

  // Blocks of the cleaned HTML, in document order, each with the original element it came from.
  const blocks = [];
  const blockOf = new Map();
  const cellInfo = new Map();
  const original = (element) => {
    const stamp = element.getAttribute(STAMP);
    return stamp === null ? null : page.elements[Number(stamp)];
  };
  for (const table of content.querySelectorAll('table')) {
    for (const [cell, info] of tableCells(table, original)) cellInfo.set(cell, info);
  }
  for (const element of content.querySelectorAll(Object.keys(BLOCK_TYPES).join(','))) {
    const stamp = element.getAttribute(STAMP);
    let parent = null;
    for (let up = element.parentElement; up; up = up.parentElement) if (blockOf.has(up)) { parent = blockOf.get(up); break; }
    const block = { number: blocks.length, type: BLOCK_TYPES[element.localName], tag: element.localName,
      represented: element.localName !== 'svg',
      element: stamp === null ? null : Number(stamp), parent, depth: parent === null ? 0 : blocks[parent].depth + 1 };
    if (cellInfo.has(element)) Object.assign(block, cellInfo.get(element));
    blockOf.set(element, block.number);
    blocks.push(block);
  }

  // Reference markdown: exactly what Defuddle's own markdown step writes for this content.
  const plain = content.cloneNode(true);
  for (const element of plain.querySelectorAll(`[${STAMP}]`)) element.removeAttribute(STAMP);
  const referenceMarkdown = createMarkdownContent(plain.innerHTML, url || 'about:blank');

  // Sentinel run: each block's text opens with START n and closes with END n, outer blocks outside inner ones.
  const inserts = new Map();          // text node -> [{at, order, text}]
  const textNodes = (element) => {
    const walker = host.createTreeWalker(element, 4);
    const found = [];
    for (let node = walker.nextNode(); node; node = walker.nextNode()) if (node.data.trim()) found.push(node);
    return found;
  };
  const addInsert = (node, at, order, text) => {
    if (!inserts.has(node)) inserts.set(node, []);
    inserts.get(node).push({ at, order, text });
  };
  const sentinels = (number) => [SENTINEL_START_OPEN + sentinelDigits(number) + SENTINEL_START_CLOSE,
    SENTINEL_END_OPEN + sentinelDigits(number) + SENTINEL_END_CLOSE];
  // An empty cell still needs a place in the markdown (an empty paragraph is not written at all). Its sentinel
  // pair goes in first, as text, so the table's own sentinels land around it and the cell nests in its table.
  const emptyCells = new Set();
  for (const [element, number] of blockOf) {
    if (blocks[number].type === 'table_cell' && !textNodes(element).length) {
      element.appendChild(host.createTextNode(sentinels(number).join('')));
      emptyCells.add(element);
    }
  }
  for (const [element, number] of blockOf) {
    if (emptyCells.has(element)) continue;
    const block = blocks[number];
    const texts = textNodes(element);
    const [open, close] = sentinels(number);
    if (!texts.length) continue;
    const first = texts[0], last = texts[texts.length - 1];
    addInsert(first, first.data.length - first.data.trimStart().length, block.depth, open);
    addInsert(last, last.data.trimEnd().length, 1e6 - block.depth, close);
  }
  for (const [node, list] of inserts) {
    list.sort((a, b) => a.at - b.at || a.order - b.order);
    let data = node.data, shift = 0;
    for (const insert of list) {
      data = data.slice(0, insert.at + shift) + insert.text + data.slice(insert.at + shift);
      shift += insert.text.length;
    }
    node.data = data;
  }
  for (const element of content.querySelectorAll(`[${STAMP}]`)) element.removeAttribute(STAMP);
  const stripped = stripSentinels(createMarkdownContent(content.innerHTML, url || 'about:blank'));
  const markedIndex = codePointIndex(stripped.markdown);

  // What Defuddle removed: subtrees in which no stamped element survived, below an ancestor in which one did.
  const hasKept = page.elements.map((_, index) => kept.has(index));
  for (let index = page.elements.length - 1; index >= 0; index--) {
    const parent = page.elements[index].parentElement;
    if (hasKept[index] && parent) hasKept[page.number.get(parent)] = true;
  }
  const removedRoot = (index) => {
    // The topmost removed ancestor-or-self of an element, or null when the element sits in kept content.
    let root = null;
    for (let element = page.elements[index]; element; element = element.parentElement) {
      const number = page.number.get(element);
      if (hasKept[number]) break;
      root = number;
    }
    return root;
  };
  // Each removed word belongs to the removal that decided it: the conservative pass's element when one holds it
  // (its reason is known), else the topmost removed subtree — Defuddle's own removal, whose reason is read from
  // markup in reader mode and is unknown (null: the words stay lost) in the default mode.
  const decidedBy = (number, root) => {
    for (let element = page.elements[number]; element; element = element.parentElement) {
      const index = page.number.get(element);
      if (removals.has(index)) return index;
      if (index === root) break;
    }
    return null;
  };
  const droppedWords = new Map();
  for (const word of inventory) {
    const root = removedRoot(word.element);
    if (root === null) continue;
    const region = decidedBy(word.element, root) ?? root;
    droppedWords.set(region, (droppedWords.get(region) || 0) + 1);
  }
  const dropped = [];
  for (const [region, words] of [...droppedWords].sort((a, b) => a[0] - b[0])) {
    const element = page.elements[region];
    const removal = removals.get(region);
    const why = removal ? { reason: removal.reason, detail: removal.detail }
      : reader ? page.dropReason(element) : { reason: null, detail: `removed by Defuddle (<${element.localName}>)` };
    dropped.push({ source_range: page.range(page.span(element)), xpath: page.xpaths.get(element), words, ...why });
  }

  const outBlocks = blocks.map((block) => {
    const element = block.element === null ? null : page.elements[block.element];
    const start = stripped.starts.get(block.number), end = stripped.ends.get(block.number);
    return {
      ...block,
      markdown_range: start === undefined || end === undefined ? null : { start: markedIndex[start], end: markedIndex[end] },
      source_range: element ? page.range(page.span(element)) : null,
      xpath: element ? page.xpaths.get(element) : null,
    };
  });
  return {
    reference_markdown: referenceMarkdown,
    marked_markdown: stripped.markdown === referenceMarkdown ? null : stripped.markdown,
    blocks: outBlocks,
    inventory,
    extra_words: page.extraWords(),
    dropped,
    kept_elements: kept.size,
    title: result.title || null,
    mapping_fallbacks: page.mappingFallbacks,
  };
}

async function main() {
  const mode = process.argv[2];
  if (mode !== 'convert' && mode !== 'inventory') {
    process.stderr.write('usage: node mdify_html.mjs convert [--reader]|inventory < {"html": "...", "url": "..."}\n');
    process.exit(2);
  }
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  const input = JSON.parse(Buffer.concat(chunks).toString('utf8'));
  const html = input.html;
  // A local file has no URL; a malformed one is treated the same (jsdom refuses it, and links stay relative).
  const url = URL.canParse(input.url || '') ? input.url : '';
  const page = new Page(html, url);
  const output = mode === 'convert' ? await convert(page, url, process.argv.includes('--reader')) : { inventory: page.inventory(), mapping_fallbacks: page.mappingFallbacks };
  process.stdout.write(JSON.stringify(output));
}

main().catch((error) => { process.stderr.write(`${error.stack || error}\n`); process.exit(1); });
