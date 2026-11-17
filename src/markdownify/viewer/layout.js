// Every pane boundary drags and is remembered; the KPI strip and the ledger inspector fold. WHY: Zach's standing UI
// rule (~/zach_brain/Settings/Memory/feedback_resizable_panels.md) — a split he sets stays where he left it.

const STORAGE_PREFIX = "markdownify-view:";

export function readStored(key, fallback) {
  try {
    const raw = window.localStorage.getItem(STORAGE_PREFIX + key);
    return raw === null ? fallback : JSON.parse(raw);
  } catch {
    return fallback;
  }
}

export function writeStored(key, value) {
  try {
    window.localStorage.setItem(STORAGE_PREFIX + key, JSON.stringify(value));
  } catch {
    // Storage full or blocked: the layout simply isn't remembered.
  }
}

const MIN_PANE = 160;
const MIN_INSPECTOR = 200;
const KEY_STEP = 16;

/**
 * source | output | inspector, side by side in `row`. The source/output boundary is kept as the source's share of
 * the two (so a wider window keeps the proportion); the inspector as its width in px and whether it is open.
 * Returns {setInspector(open), toggleInspector(), get inspectorOpen}; `onChange` hears every settled change.
 */
export function splitPanes(row, { source, output, inspector, sourceHandle, inspectorHandle, onChange }) {
  const state = {
    share: readStored("layout:source-share", 0.5),
    inspectorWidth: readStored("layout:inspector-width", 280),
    inspectorOpen: readStored("layout:inspector-open", false),
  };

  const available = () => row.clientWidth - sourceHandle.offsetWidth - (state.inspectorOpen ? inspectorHandle.offsetWidth + state.inspectorWidth : 0);

  function apply() {
    const room = available();
    const sourceWidth = Math.round(Math.min(Math.max(state.share * room, MIN_PANE), Math.max(room - MIN_PANE, MIN_PANE)));
    source.style.flex = `0 0 ${sourceWidth}px`;
    output.style.flex = "1 1 0";
    inspector.hidden = !state.inspectorOpen;
    inspectorHandle.hidden = !state.inspectorOpen;
    inspector.style.flex = `0 0 ${state.inspectorWidth}px`;
    row.dataset.inspector = state.inspectorOpen ? "open" : "folded";
    sourceHandle.setAttribute("aria-valuenow", String(Math.round(state.share * 100)));
  }

  function settle() {
    writeStored("layout:source-share", state.share);
    writeStored("layout:inspector-width", state.inspectorWidth);
    writeStored("layout:inspector-open", state.inspectorOpen);
    onChange?.(state);
  }

  function moveSource(width) {
    const room = available();
    state.share = Math.min(Math.max(width, MIN_PANE), room - MIN_PANE) / room;
    apply();
  }

  function moveInspector(width) {
    const most = row.clientWidth - sourceHandle.offsetWidth - inspectorHandle.offsetWidth - 2 * MIN_PANE;
    state.inspectorWidth = Math.round(Math.min(Math.max(width, MIN_INSPECTOR), Math.max(most, MIN_INSPECTOR)));
    apply();
  }

  function drag(handle, startWidth, move, sign) {
    handle.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) return;
      event.preventDefault();
      handle.setPointerCapture(event.pointerId);
      const x = event.clientX;
      const width = startWidth();
      row.dataset.dragging = "";
      const onMove = (moved) => move(width + sign * (moved.clientX - x));
      const onUp = () => {
        handle.removeEventListener("pointermove", onMove);
        handle.removeEventListener("pointerup", onUp);
        handle.removeEventListener("pointercancel", onUp);
        delete row.dataset.dragging;
        settle();
      };
      handle.addEventListener("pointermove", onMove);
      handle.addEventListener("pointerup", onUp);
      handle.addEventListener("pointercancel", onUp);
    });
    // The keyboard moves a boundary too (the separator is focusable).
    handle.addEventListener("keydown", (event) => {
      const step = event.key === "ArrowLeft" ? -KEY_STEP : event.key === "ArrowRight" ? KEY_STEP : 0;
      if (!step) return;
      event.preventDefault();
      move(startWidth() + sign * step);
      settle();
    });
  }

  drag(sourceHandle, () => source.getBoundingClientRect().width, moveSource, 1);
  drag(inspectorHandle, () => state.inspectorWidth, moveInspector, -1);

  new ResizeObserver(apply).observe(row);
  apply();

  return {
    get inspectorOpen() {
      return state.inspectorOpen;
    },
    setInspector(open) {
      state.inspectorOpen = open;
      apply();
      settle();
    },
    toggleInspector() {
      this.setInspector(!state.inspectorOpen);
    },
  };
}

/** A boolean remembered per key; `onChange(open)` draws it, now and on every change. Returns {get open, set, toggle}. */
export function foldable(key, fallback, onChange) {
  let open = readStored(key, fallback);
  const control = {
    get open() {
      return open;
    },
    set(next) {
      open = next;
      writeStored(key, open);
      onChange(open);
    },
    toggle() {
      control.set(!open);
    },
  };
  onChange(open);
  return control;
}
