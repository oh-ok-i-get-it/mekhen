// Pan and zoom with a CSS transform on each panel's canvas. A view is {x, y, k}; when synced, both
// panels share one view object, so moving either moves both.

const MIN_SCALE = 0.05;
const MAX_SCALE = 8;
const PADDING = 24;

export function newView() {
  return { x: 0, y: 0, k: 1, fitted: false };
}

export function clampScale(k) {
  return Math.min(MAX_SCALE, Math.max(MIN_SCALE, k));
}

export function apply(canvas, view) {
  canvas.style.transform = `translate(${view.x}px, ${view.y}px) scale(${view.k})`;
}

// Zoom by `factor` keeping the point (px, py), in viewport pixels, fixed on screen.
export function zoomAt(view, factor, px, py) {
  const k = clampScale(view.k * factor);
  const ratio = k / view.k;
  view.x = px - (px - view.x) * ratio;
  view.y = py - (py - view.y) * ratio;
  view.k = k;
}

// Fit content of size (w, h) into a viewport of size (vw, vh), centred, never enlarging past 1:1.
// With several panels sharing one view, every diagram must fit: the smallest scale wins, and the
// largest diagram is centred.
export function fitView(view, sizes) {
  const usable = sizes.filter((s) => s.w > 0 && s.h > 0 && s.vw > 0 && s.vh > 0);
  if (!usable.length) return;
  const k = clampScale(Math.min(1, ...usable.map((s) => Math.min((s.vw - 2 * PADDING) / s.w, (s.vh - 2 * PADDING) / s.h))));
  const vw = Math.min(...usable.map((s) => s.vw));
  const vh = Math.min(...usable.map((s) => s.vh));
  const w = Math.max(...usable.map((s) => s.w));
  const h = Math.max(...usable.map((s) => s.h));
  view.k = k;
  view.x = Math.max(PADDING, (vw - w * k) / 2);
  view.y = Math.max(PADDING, (vh - h * k) / 2);
  view.fitted = true;
}

// Wire wheel, drag and double-click on one viewport. getView() returns the view to change (it
// may be shared); onChange() re-applies transforms wherever that view is shown.
export function attach(viewport, { getView, onChange, onFit }) {
  viewport.addEventListener(
    "wheel",
    (event) => {
      event.preventDefault();
      const view = getView();
      if (!view) return;
      const rect = viewport.getBoundingClientRect();
      const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? rect.height : 1;
      zoomAt(view, Math.exp(-event.deltaY * unit * 0.0015), event.clientX - rect.left, event.clientY - rect.top);
      onChange();
    },
    { passive: false },
  );

  let drag = null;
  viewport.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    const view = getView();
    if (!view) return;
    drag = { id: event.pointerId, x: event.clientX, y: event.clientY };
    viewport.setPointerCapture(event.pointerId);
    viewport.classList.add("dragging");
  });
  viewport.addEventListener("pointermove", (event) => {
    if (!drag || drag.id !== event.pointerId) return;
    const view = getView();
    view.x += event.clientX - drag.x;
    view.y += event.clientY - drag.y;
    drag.x = event.clientX;
    drag.y = event.clientY;
    onChange();
  });
  const end = (event) => {
    if (!drag || drag.id !== event.pointerId) return;
    drag = null;
    viewport.classList.remove("dragging");
  };
  viewport.addEventListener("pointerup", end);
  viewport.addEventListener("pointercancel", end);
  viewport.addEventListener("dblclick", () => onFit());
}
