// Markdownify's own view of one bundle: the source on the left, the markdown that came out of it on the right, every
// word linked both ways, and the KPIs it is graded by on top (CONTRACT §3–§6) — the evaluation pipeline, visible.

import { foldable, readStored, splitPanes, writeStored } from "./layout.js";
import { attachCells, cellRanges, codePointToUtf16Map, linkedWords, parseMarkdown, renderDocument, renderRaw } from "./markdown.js";
import { sourceKindOf, sourcePane } from "./source.js";

/** The four styles the picker offers (source-highlighter CONTRACT §3; underline is left out of the picker). */
const PICKER_STYLES = ["highlight", "box", "spotlight", "blackout"];

const LADDER_LABELS = {
  prose: "Prose",
  simple_table: "Simple table",
  complex_table: "Complex table",
  plot: "Plot",
  diagram: "Diagram",
  figure: "Figure",
};

const $ = (selector) => document.querySelector(selector);
const percent = (value) => (value === null || value === undefined ? "n/a" : `${(value * 100).toFixed(value === 1 ? 0 : 1)}%`);
const count = (value) => (value === null || value === undefined ? "n/a" : value.toLocaleString());
const isBox = (location) => !!location && "box" in location;

function element(tag, attributes = {}, ...children) {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(attributes)) {
    if (value === undefined || value === null || value === false) continue;
    if (name === "className") node.className = value;
    else node.setAttribute(name, value === true ? "" : String(value));
  }
  node.append(...children.filter((child) => child !== null && child !== undefined));
  return node;
}

async function loadBundle() {
  const manifest = await (await fetch("/bundle")).json();
  const file = async (name) => {
    const reply = await fetch(`/bundle/${name}`);
    if (!reply.ok) throw new Error(`${name}: HTTP ${reply.status}`);
    return name.endsWith(".json") ? reply.json() : reply.text();
  };
  const [markdown, index, ledger, kpis] = await Promise.all([file("document.md"), file("index.json"), file("ledger.json"), file("kpis.json")]);
  return { manifest, markdown, index, ledger, kpis };
}

// ---- the KPI strip ---------------------------------------------------------------------------------------------

/** The one calm answer the strip leads with; amber only on what misses its target (CONTRACT §6). */
function headline(kpis) {
  const lost = kpis.silently_lost;
  const unlinked = kpis.unlinked;
  const clean = (lost ?? 0) === 0 && (unlinked ?? 0) === 0 && (kpis.coverage === null || kpis.coverage === 1) && kpis.traceability === 1;
  if (clean) return [`Every word accounted for${kpis.coverage === null ? " (no source inventory for this kind of file)" : ""}`];
  return [
    `${percent(kpis.coverage)} coverage · ${percent(kpis.traceability)} traceable`,
    lost ? element("span", { className: "warn" }, ` · ${count(lost)} lost`) : null,
    unlinked ? element("span", { className: "warn" }, ` · ${count(unlinked)} unlinked`) : null,
  ];
}

function kpi(testId, label, value, warn) {
  return element("div", { className: "kpi", "data-testid": testId },
    element("span", { className: "kpi-label" }, label),
    element("span", { className: `kpi-value${warn ? " warn" : ""}` }, value));
}

function kpiDetail(kpis) {
  const time = kpis.time ?? { seconds: 0 };
  const timeText = `${time.seconds.toFixed(1)} s${time.seconds_per_page ? ` · ${time.seconds_per_page.toFixed(2)} s/page` : ""}${time.cold ? " · cold" : ""}`;
  const rungs = Object.entries(kpis.ladder ?? {}).map(([key, rung]) =>
    element("span", { className: "rung", "data-rung": key, title: `${LADDER_LABELS[key] ?? key}: ${rung.represented} of ${rung.seen} represented` },
      element("span", { className: "kpi-label-inline" }, LADDER_LABELS[key] ?? key), ` ${rung.represented}/${rung.seen}`));
  return [
    kpi("kpi-time", "Time", timeText),
    kpi("kpi-coverage", "Coverage", percent(kpis.coverage), kpis.coverage !== null && kpis.coverage < 1),
    kpi("kpi-traceability", "Traceability", percent(kpis.traceability), kpis.traceability !== null && kpis.traceability < 1),
    kpi("kpi-lost", "Lost", count(kpis.silently_lost), !!kpis.silently_lost),
    kpi("kpi-unlinked", "Unlinked", count(kpis.unlinked), !!kpis.unlinked),
    element("div", { className: "kpi", "data-testid": "kpi-ladder" },
      element("span", { className: "kpi-label" }, "Representation ladder"),
      element("span", { className: "kpi-value rungs" }, ...rungs)),
  ];
}

// ---- the ledger inspector --------------------------------------------------------------------------------------

function ledgerInspector(ledger, onLost, onUnlinked) {
  const lost = ledger.source.lost_words ?? [];
  const unlinked = ledger.markdown.unlinked_words ?? [];
  const lostItems = lost.map((word, index) => element("li", {},
    element("button", { type: "button", "data-lost": index, title: word.text },
      element("span", { className: "warn" }, word.text),
      isBox(word.grounding) ? element("span", { className: "muted" }, ` · p. ${word.grounding.page ?? 1}`) : null)));
  const unlinkedItems = unlinked.map((word, index) => element("li", {},
    element("button", { type: "button", "data-unlinked-item": index, title: word.text },
      element("span", { className: "warn" }, word.text),
      element("span", { className: "muted" }, ` · @${word.range.start}`))));
  const section = (title, empty, items) => element("section", {},
    element("h3", {}, title),
    items.length ? element("ul", {}, ...items) : element("p", { className: "muted" }, empty));
  const root = element("div", { className: "ledger" },
    section(`Lost in the source (${ledger.source.available ? lost.length : "no inventory"})`,
      ledger.source.available ? "None." : "This kind of file has no source inventory yet.", lostItems),
    section(`Unlinked in the markdown (${unlinked.length})`, "None.", unlinkedItems));
  root.addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    if (button.dataset.lost !== undefined) onLost(Number(button.dataset.lost));
    else if (button.dataset.unlinkedItem !== undefined) onUnlinked(Number(button.dataset.unlinkedItem));
  });
  return root;
}

// ---- source → words ----------------------------------------------------------------------------------------------

/** Words whose grounding contains a point on the source. */
function wordsAt(point, words, probed) {
  const hits = [];
  words.forEach((word, index) => {
    const grounding = word.grounding;
    if (!grounding) return;
    if (isBox(grounding)) {
      const [x0, y0, x1, y1] = grounding.box;
      if ((grounding.page ?? 1) === point.page && point.x >= x0 && point.x <= x1 && point.y >= y0 && point.y <= y1) hits.push(index);
      return;
    }
    for (const mark of probed.get(index) ?? []) {
      const { x0, y0, x1, y1 } = mark.rect;
      if (mark.page === point.page && point.sourceX >= x0 && point.sourceX <= x1 && point.sourceY >= y0 && point.sourceY <= y1) {
        hits.push(index);
        return;
      }
    }
  });
  return hits;
}

// ---- the full archive ----------------------------------------------------------------------------------------------

/** "Full archive": the bundle's source.wacz in ReplayWeb.page (archive.html), at the captured URL and capture time
 * (CONTRACT §2.1). It opens in its own tab, because the replay is a whole browser of its own (pages, resources). */
async function archiveButton(after) {
  const reply = await fetch("/bundle/source.json");
  const record = reply.ok ? await reply.json() : {};
  const capture = [...(record.retrievals ?? [])].reverse().find((retrieval) => retrieval.capture) ?? {};
  const params = new URLSearchParams();
  if (capture.final_url) params.set("url", capture.final_url);
  // ReplayWeb.page's ts is the 14-digit WARC timestamp: 2026-11-05T20:04:01Z → 20261105200401.
  if (capture.retrieved_at) params.set("ts", capture.retrieved_at.replace(/\D/g, "").slice(0, 14));
  after.after(element("a", { className: "toggle", href: `/static/archive.html?${params}`, target: "_blank", rel: "noopener",
    "data-testid": "archive-open", title: "Replay every response of the capture offline (ReplayWeb.page)" }, "Full archive"));
}

// ---- the page ------------------------------------------------------------------------------------------------------

function fail(message) {
  $("#app").replaceChildren(element("div", { className: "failed", "data-testid": "viewer-failed" },
    element("h2", {}, "Markdownify could not open this bundle"), element("p", {}, message)));
}

async function main() {
  let bundle;
  try {
    bundle = await loadBundle();
  } catch (error) {
    return fail(String(error?.message ?? error));
  }
  const { manifest, markdown, index, ledger, kpis } = bundle;
  document.title = `${manifest.name ?? manifest.source} · Markdownify`;
  const viewer = $("#viewer");
  viewer.dataset.sha = manifest.sha256;
  viewer.hidden = false;
  $("#loading").remove();

  // One markdown word: from index.json (with its grounding) or from the ledger's unlinked list (none).
  const words = [
    ...index.words.map((word) => ({ range: word.range, grounding: word.grounding })),
    ...(ledger.markdown.unlinked_words ?? []).map((word) => ({ range: word.range, unlinked: true })),
  ];
  const firstUnlinked = index.words.length;
  const toUtf16 = codePointToUtf16Map(markdown);
  const linked = linkedWords(words, toUtf16);
  const unlinkedSet = new Set(words.flatMap((word, at) => (word.unlinked ? [at] : [])));
  const nodes = parseMarkdown(markdown);
  attachCells(nodes, cellRanges(index.blocks, toUtf16));
  const blocks = new Map(index.blocks.map((block) => [block.id, block]));

  // ---- the strip
  const strip = $("[data-testid=kpi-strip]");
  $("[data-testid=kpi-headline]").replaceChildren(...headline(kpis));
  $("[data-testid=kpi-detail]").replaceChildren(...kpiDetail(kpis));
  const kpisOpen = foldable("kpis-open", true, (open) => {
    strip.dataset.open = open ? "true" : "false";
    $("[data-testid=kpi-detail]").hidden = !open;
    $("[data-testid=kpi-fold]").setAttribute("aria-expanded", String(open));
    $("[data-testid=kpi-fold]").setAttribute("aria-label", open ? "Fold the KPIs" : "Show the KPIs");
  });
  $("[data-testid=kpi-fold]").addEventListener("click", () => kpisOpen.toggle());

  let style = readStored("style", "highlight");
  if (!PICKER_STYLES.includes(style)) style = "highlight";
  const picker = $("[data-testid=style-picker]");
  picker.replaceChildren(...PICKER_STYLES.map((name) => element("button", { type: "button", "data-style": name }, name)));
  const drawPicker = () => {
    for (const button of picker.querySelectorAll("button")) button.setAttribute("aria-pressed", String(button.dataset.style === style));
  };
  drawPicker();

  // ---- the panes
  const panes = $("[data-testid=panes]");
  const split = splitPanes(panes, {
    source: $("[data-testid=source-pane]"),
    output: $("[data-testid=markdown-pane]"),
    inspector: $("[data-testid=ledger-inspector]"),
    sourceHandle: $("[data-testid=splitter-source-output]"),
    inspectorHandle: $("[data-testid=splitter-output-inspector]"),
    onChange: (state) => drawInspectorToggle(state.inspectorOpen),
  });
  const inspectorToggle = $("[data-testid=inspector-toggle]");
  const drawInspectorToggle = (open) => {
    inspectorToggle.setAttribute("aria-pressed", String(open));
  };
  drawInspectorToggle(split.inspectorOpen);
  inspectorToggle.addEventListener("click", () => split.toggleInspector());
  if (manifest.files.includes("source.wacz")) archiveButton(inspectorToggle).catch(() => {});

  // ---- left: the source
  const sourceKind = sourceKindOf(manifest.media_type);
  let probed = new Map();
  let shownLocations = [];
  // WHY no layer when nothing is selected: spotlight and blackout cover everything outside their marks, so an empty
  // selection would veil the whole source before the first click.
  const layersOf = (locations) => (locations.length ? [{ locations, style }] : []);
  const source = sourcePane($("[data-testid=source-pane]"), {
    source: { kind: sourceKind, url: `/bundle/${manifest.source}` },
    probe: sourceKind === "html" ? words.flatMap((word, at) => (word.grounding ? [{ ...word.grounding, id: `w${at}` }] : [])) : undefined,
    onProbe: (marks) => {
      probed = new Map();
      for (const mark of marks) {
        const at = Number(String(mark.id).slice(1));
        probed.set(at, [...(probed.get(at) ?? []), mark]);
      }
    },
    onPoint: (point) => {
      const hits = wordsAt(point, words, probed);
      lightWords(hits, true);
      shownLocations = hits.flatMap((at, order) => (words[at].grounding ? [{ ...words[at].grounding, id: `sel-${order}` }] : []));
      source.show(layersOf(shownLocations), null);
    },
  });

  const showOnSource = (locations) => {
    shownLocations = locations.map((location, order) => ({ ...location, id: `sel-${order}` }));
    source.show(layersOf(shownLocations), shownLocations.length ? "sel-0" : null);
  };

  picker.addEventListener("click", (event) => {
    const button = event.target.closest("[data-style]");
    if (!button) return;
    style = button.dataset.style;
    writeStored("style", style);
    drawPicker();
    source.show(layersOf(shownLocations), null);
  });

  // ---- right: the markdown
  const doc = $("[data-testid=markdown-pane]");
  let raw = readStored("raw", false);
  const rawToggle = $("[data-testid=raw-toggle]");
  let selected = [];

  // WHY the selection is painted on the DOM attributes, not by rendering again: a datasheet renders thousands of
  // word spans, and lighting one word must not rebuild them.
  function paintSelection(scroll) {
    for (const lit of doc.querySelectorAll("[data-selected]")) lit.removeAttribute("data-selected");
    let first = null;
    for (const word of selected) {
      for (const span of doc.querySelectorAll(`[data-w="${word}"]`)) {
        span.setAttribute("data-selected", "");
        first ??= span;
      }
    }
    // `inline: 'center'` too: a datasheet's spec table is wider than the pane, and its model columns sit far right.
    if (scroll) first?.scrollIntoView({ block: "center", inline: "center", behavior: "smooth" });
  }

  function lightWords(list, scroll) {
    selected = list;
    paintSelection(scroll);
  }

  function drawDocument() {
    doc.dataset.mode = raw ? "raw" : "rendered";
    rawToggle.setAttribute("aria-pressed", String(raw));
    doc.replaceChildren(raw ? renderRaw(markdown, linked, unlinkedSet) : renderDocument(nodes, linked, unlinkedSet));
    paintSelection(false);
  }
  drawDocument();
  rawToggle.addEventListener("click", () => {
    raw = !raw;
    writeStored("raw", raw);
    drawDocument();
  });

  doc.addEventListener("click", (event) => {
    const span = event.target.closest("[data-w]");
    if (span) {
      const word = words[Number(span.dataset.w)];
      lightWords([Number(span.dataset.w)], false);
      showOnSource(word.grounding ? [word.grounding] : []);
      return;
    }
    const cellElement = event.target.closest("[data-cell]");
    const cell = cellElement && blocks.get(cellElement.dataset.cell);
    if (!cell) return;
    lightWords(words.flatMap((word, at) => (word.range.start >= cell.range.start && word.range.end <= cell.range.end ? [at] : [])), false);
    showOnSource(cell.grounding ?? []);
  });

  // ---- the ledger
  $("[data-testid=ledger-inspector]").replaceChildren(ledgerInspector(ledger,
    (at) => {
      const lost = ledger.source.lost_words?.[at];
      lightWords([], false);
      showOnSource(lost ? [lost.grounding] : []);
    },
    (at) => {
      lightWords([firstUnlinked + at], true);
      showOnSource([]);
    }));
}

main();
