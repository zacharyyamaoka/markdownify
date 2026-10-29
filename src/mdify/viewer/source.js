// The left pane: the Source Highlighter (source-highlighter/CONTRACT.md §4.2) drawing the source and its marks.
// This file only owns the module's lifecycle and turns clicks on the source into source points; the rendering, the
// geometry and the styles are the highlighter's.

import { highlight } from "./vendor/source-highlighter.js";

const PDFJS = new URL("./vendor/pdfjs/pdf.min.mjs", import.meta.url).href;

/** WHY the probe style: an invisible box per word, drawn once, is how an archived HTML page's words get rectangles
 * to hit-test a click against — the highlighter is the only thing that knows where an html Location lands. */
const PROBE_STYLE = { name: "box", width: 0, opacity: 0, padding: 0 };

/** Which renderer the highlighter needs for a bundle's source, by its media type. */
export function sourceKindOf(mediaType) {
  if (mediaType === "application/pdf") return "pdf";
  if (mediaType.startsWith("image/")) return "image";
  if (mediaType === "text/html") return "html";
  return "text";
}

/**
 * Draw `source` ({kind, url}) into `container`. `probe` (html sources) is measured once after the source renders and
 * handed to `onProbe(marks)`; a click on a page calls `onPoint({page, x, y, sourceX, sourceY})` — x/y as 0–1 of the
 * page, sourceX/Y in source units. Returns {show(layers, focusId), ready}.
 */
export function sourcePane(container, { source, probe, onProbe, onPoint }) {
  container.dataset.kind = source.kind;
  container.dataset.state = "loading";
  let handle = null;
  let latest = { layers: [], focusId: null };

  const ready = (async () => {
    const spec = source.kind === "html" || source.kind === "text"
      ? { kind: source.kind, [source.kind]: await (await fetch(source.url)).text() }
      : { kind: source.kind, url: source.url };
    handle = highlight(container, { source: spec, layers: [], pdfjs: PDFJS, zoom: "fit" });
    await handle.ready;
    if (source.kind === "html") {
      // WHY a catcher over the archived page: it is a sandboxed, script-free iframe, so a click inside it never
      // reaches this document. A transparent layer over it takes the click; the marks still draw above it.
      for (const page of container.querySelectorAll(".sh-page")) {
        const catcher = document.createElement("div");
        catcher.dataset.catcher = "";
        Object.assign(catcher.style, { position: "absolute", inset: "0", cursor: "crosshair" });
        page.appendChild(catcher);
      }
    }
    if (probe?.length && onProbe) {
      const probed = await handle.update({ layers: [{ locations: probe, style: PROBE_STYLE }] });
      onProbe(probed.marks);
    }
    await handle.update({ layers: latest.layers });
    container.dataset.state = "ready";
  })();
  ready.catch((error) => {
    container.dataset.state = "failed";
    container.dataset.error = String(error?.message ?? error);
    container.replaceChildren(Object.assign(document.createElement("p"), {
      className: "pane-failed",
      textContent: `The source could not be drawn: ${error?.message ?? error}`,
    }));
  });

  // WHY a queue: marks change through `update`, never by drawing the source again, and two quick clicks must land
  // in order — the second click's marks are the ones left on screen.
  let queue = ready;
  let serial = 0;
  function show(layers, focusId) {
    latest = { layers, focusId };
    const mine = ++serial;
    queue = queue.then(async () => {
      if (latest.layers !== layers) return; // a newer ask is queued behind this one
      await handle.update({ layers });
      if (focusId) await handle.focus(focusId, { behavior: "smooth" });
      container.dataset.shown = String(mine); // what a test waits on: these marks are drawn
    }).catch(() => {});
    return queue;
  }

  // Fit the page to the pane again once a drag settles (the highlighter fits at render time only).
  let timer = 0;
  let width = container.clientWidth;
  new ResizeObserver(() => {
    if (Math.abs(container.clientWidth - width) < 2) return;
    width = container.clientWidth;
    window.clearTimeout(timer);
    timer = window.setTimeout(() => {
      // WHY focus() with an id no mark has: it is the module's one public door to "zoom: fit" after render.
      container.dataset.state = "fitting"; // a PDF draws every page again; the marks are back when it is "ready"
      queue = queue.then(() => handle?.focus("\u0000refit", { zoom: "fit" })).catch(() => {}).then(() => {
        if (container.dataset.state === "fitting") container.dataset.state = "ready";
      });
    }, 200);
  }).observe(container);

  container.addEventListener("click", (event) => {
    const page = event.target.closest(".sh-page");
    if (!page || !handle) return;
    const rect = page.getBoundingClientRect();
    const zoom = handle.zoom || 1;
    onPoint?.({
      page: Number(page.dataset.page),
      x: (event.clientX - rect.left) / rect.width,
      y: (event.clientY - rect.top) / rect.height,
      sourceX: (event.clientX - rect.left) / zoom,
      sourceY: (event.clientY - rect.top) / zoom,
    });
  });

  return { show, ready };
}
