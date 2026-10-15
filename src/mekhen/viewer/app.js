import { createHost } from "./host.js";
import * as pz from "./panzoom.js";
import { initMermaid, render } from "./render.js";

const host = createHost();
const ROLES = ["baseline", "proposed"];
const VIEWS = ["side", "baseline", "proposed"];
const STATUS_LABELS = {
  both: "both sides",
  "baseline-only": "baseline only",
  "proposed-only": "proposed only",
  "lint-error": "error",
  generated: "generated",
  stale: "stale",
};

const $ = (selector, root = document) => root.querySelector(selector);

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child !== null && child !== undefined && child !== false) node.append(child);
  }
  return node;
}

const prefs = {
  load() {
    try {
      return JSON.parse(localStorage.getItem("mekhen.prefs") || "{}");
    } catch {
      return {};
    }
  },
  save(values) {
    try {
      localStorage.setItem("mekhen.prefs", JSON.stringify(values));
    } catch {
      /* private window or storage blocked: preferences just aren't remembered */
    }
  },
};

const saved = prefs.load();
const urlView = new URLSearchParams(location.search).get("view");
const state = {
  project: null,
  list: [],
  current: null,
  detail: null,
  view: VIEWS.includes(urlView) ? urlView : VIEWS.includes(saved.view) ? saved.view : "side",
  highlight: saved.highlight !== false,
  sync: saved.sync !== false,
  views: new Map(), // diagram id -> {shared, baseline, proposed, fitted}
  rendered: { baseline: null, proposed: null }, // {key, id, ok, w, h}
  everConnected: false,
  loadToken: 0,
};

const panels = Object.fromEntries(
  ROLES.map((role) => {
    const root = $(`.panel[data-role="${role}"]`);
    return [
      role,
      {
        root,
        viewport: $(".viewport", root),
        canvas: $(".canvas", root),
        error: $(".panel-error", root),
        empty: $(".panel-empty", root),
        origin: $(".origin", root),
        path: $(".path", root),
      },
    ];
  }),
);

// -- views (pan / zoom) ------------------------------------------------------------------------

function viewsFor(id) {
  if (!state.views.has(id)) {
    state.views.set(id, { shared: pz.newView(), baseline: pz.newView(), proposed: pz.newView(), fitted: false });
  }
  return state.views.get(id);
}

function viewFor(role) {
  if (!state.current) return null;
  const views = viewsFor(state.current);
  return state.sync ? views.shared : views[role];
}

function visibleRoles() {
  return state.view === "side" ? ROLES : [state.view];
}

function applyAll() {
  for (const role of ROLES) {
    const view = viewFor(role);
    if (view) pz.apply(panels[role].canvas, view);
  }
}

function sizeOf(role) {
  const r = state.rendered[role];
  const vp = panels[role].viewport;
  if (!r || r.id !== state.current) return null;
  return { w: r.w, h: r.h, vw: vp.clientWidth, vh: vp.clientHeight };
}

function fit() {
  if (!state.current) return;
  const views = viewsFor(state.current);
  const roles = visibleRoles();
  if (state.sync) {
    pz.fitView(views.shared, roles.map(sizeOf).filter(Boolean));
  } else {
    for (const role of roles) {
      const size = sizeOf(role);
      if (size) pz.fitView(views[role], [size]);
    }
  }
  views.fitted = true;
  applyAll();
}

function zoomBy(factor) {
  const targets = new Set(visibleRoles().map(viewFor).filter(Boolean));
  const vp = panels[visibleRoles()[0]].viewport;
  for (const view of targets) pz.zoomAt(view, factor, vp.clientWidth / 2, vp.clientHeight / 2);
  applyAll();
}

function actualSize() {
  const targets = new Set(visibleRoles().map(viewFor).filter(Boolean));
  const vp = panels[visibleRoles()[0]].viewport;
  for (const view of targets) pz.zoomAt(view, 1 / view.k, vp.clientWidth / 2, vp.clientHeight / 2);
  applyAll();
}

for (const role of ROLES) {
  pz.attach(panels[role].viewport, { getView: () => viewFor(role), onChange: applyAll, onFit: fit });
}

// -- rendering ---------------------------------------------------------------------------------

function numbered(text) {
  return text
    .split("\n")
    .map((line, i) => `${String(i + 1).padStart(3, " ")}  ${line}`)
    .join("\n");
}

function sizeSvg(svg) {
  const box = svg.viewBox && svg.viewBox.baseVal;
  let w = box && box.width;
  let h = box && box.height;
  if (!w || !h) {
    const bbox = svg.getBBox();
    w = bbox.width;
    h = bbox.height;
  }
  svg.setAttribute("width", String(w));
  svg.setAttribute("height", String(h));
  svg.style.maxWidth = "none";
  return { w, h };
}

async function renderPanel(role, token) {
  const panel = panels[role];
  const side = state.detail && state.detail[role];
  if (!side) {
    panel.canvas.replaceChildren();
    panel.error.hidden = true;
    panel.empty.hidden = false;
    const name = state.current;
    const store = state.project ? state.project.store.description : "the store";
    panel.empty.replaceChildren(
      el("p", { class: "empty-title", text: `No ${role} file for ${name}.` }),
      el("p", {}, "Create it with ", el("code", { text: `mekhen new ${name}` }), ` (only missing files are created), in ${store}.`),
    );
    state.rendered[role] = null;
    return;
  }
  panel.empty.hidden = true;
  const source = state.highlight ? side.render : side.text;
  const key = `${state.current}\u0000${source}`;
  const previous = state.rendered[role];
  if (previous && previous.key === key) return;

  const result = await render(source);
  if (token !== state.loadToken) return; // a newer load replaced this one while Mermaid worked

  if (result.svg) {
    panel.canvas.innerHTML = result.svg; // Mermaid output, sanitised by securityLevel "strict"
    panel.canvas.classList.remove("stale");
    panel.error.hidden = true;
    const svg = panel.canvas.querySelector("svg");
    const size = svg ? sizeSvg(svg) : { w: 0, h: 0 };
    state.rendered[role] = { key, id: state.current, ok: true, ...size };
    return;
  }

  panel.error.hidden = false;
  panel.error.replaceChildren(
    el("strong", { text: "Mermaid could not render this file" }),
    el("pre", { text: result.error }),
  );
  if (previous && previous.ok && previous.id === state.current) {
    panel.canvas.classList.add("stale"); // keep the last good picture, dimmed
    state.rendered[role] = { ...previous, key };
  } else {
    const pre = el("pre", { class: "source", text: numbered(side.text) });
    panel.canvas.replaceChildren(pre);
    panel.canvas.classList.remove("stale");
    state.rendered[role] = { key, id: state.current, ok: false, w: pre.scrollWidth, h: pre.scrollHeight };
  }
}

function renderHeads(detail) {
  for (const role of ROLES) {
    const side = detail && detail[role];
    const panel = panels[role];
    // Left-to-right marks keep a leading "." in place inside the right-to-left truncating box.
    panel.path.textContent = side ? `‎${side.path}‎` : "";
    panel.path.title = side ? side.path : "";
    if (side && role === "baseline" && side.origin !== "unknown") {
      panel.origin.hidden = false;
      panel.origin.textContent = side.origin;
      panel.origin.dataset.origin = side.origin;
      panel.origin.title = side.generator ? `mekhen gen ${side.generator}` : "";
    } else {
      panel.origin.hidden = true;
    }
  }
}

function changeLine(state_, kind, item) {
  const sign = { added: "+", removed: "−", changed: "~" }[state_];
  let text;
  if (kind === "edge") {
    const label = item.label ? ` "${item.label}"` : "";
    text = `${item.src} ${item.arrow || "-->"} ${item.dst}${label}`;
  } else {
    const label = item.label ?? item.title;
    text = label && label !== item.id ? `${item.id} "${label}"` : item.id;
  }
  if (item.what) {
    const details = item.what.map((what) => {
      const key = what === "moved" ? "parent" : what;
      const before = item.before[key] ?? "(none)";
      const after = item.after[key] ?? "(none)";
      return `${what}: "${before}" to "${after}"`;
    });
    text += ` — ${details.join("; ")}`;
  }
  return el("li", { class: `change ${state_}` }, el("span", { class: "sign", text: sign }), el("span", { class: "kind", text: kind }), el("span", { text }));
}

function renderSummary(detail) {
  const body = $("#summary-body");
  const counts = $("#summary-counts");
  body.replaceChildren();
  counts.textContent = "";
  if (!detail) return;
  const diff = detail.diff;
  if (!detail.baseline || !detail.proposed) {
    body.append(el("p", { class: "note", text: "Only one side exists, so there is nothing to compare yet." }));
  } else if (diff && !diff.supported) {
    body.append(el("p", { class: "note", text: `No change highlighting: ${diff.reason}.` }));
  } else if (diff) {
    const c = diff.counts;
    counts.textContent = `+${c.added} −${c.removed} ~${c.changed}`;
    const list = el("ul", { class: "changes" });
    for (const s of ["added", "removed", "changed"]) {
      for (const item of diff.nodes[s]) list.append(changeLine(s, "node", item));
      for (const item of diff.subgraphs[s]) list.append(changeLine(s, "subgraph", item));
      for (const item of diff.edges[s]) list.append(changeLine(s, "edge", item));
    }
    if (list.children.length) body.append(list);
    else body.append(el("p", { class: "note", text: "No differences between baseline and proposed." }));
    for (const hint of diff.hints) body.append(el("p", { class: "hint", text: `Hint: ${hint}` }));
  }
  if (detail.lint.length) {
    body.append(el("h3", { text: "Issues" }));
    body.append(
      el(
        "ul",
        { class: "issues" },
        detail.lint.map((issue) =>
          el(
            "li",
            { class: `issue ${issue.severity}` },
            el("span", { class: "sev", text: issue.severity }),
            el("span", { class: "where", text: `${issue.path || ""}${issue.line ? `:${issue.line}` : ""}` }),
            el("span", { text: issue.message }),
          ),
        ),
      ),
    );
  }
}

function showMessage(...content) {
  const box = $("#main-message");
  box.hidden = content.length === 0;
  box.replaceChildren(...content);
  $("#panels").classList.toggle("has-message", content.length > 0);
}

async function loadCurrent() {
  const token = ++state.loadToken;
  const id = state.current;
  if (!id) {
    state.detail = null;
    renderHeads(null);
    renderSummary(null);
    $("#diagram-title").textContent = " ";
    if (!state.list.length) {
      const store = state.project ? state.project.store.description : "";
      showMessage(
        el("h2", { text: "No diagrams yet" }),
        el("p", {}, "Create a pair with ", el("code", { text: "mekhen new NAME" }), ", or generate the import graph with ", el("code", { text: "mekhen gen imports" }), "."),
        el("p", { class: "muted", text: `Store: ${store}. New files appear here as soon as they are saved.` }),
      );
    }
    return;
  }
  let detail;
  try {
    detail = await host.getDiagram(id);
  } catch (error) {
    showMessage(el("h2", { text: "Could not load the diagram" }), el("p", { text: String(error) }));
    return;
  }
  if (token !== state.loadToken) return;
  state.detail = detail;
  if (!detail) {
    renderHeads(null);
    renderSummary(null);
    showMessage(el("h2", { text: `No diagram named ${id}` }), el("p", { text: "It may have been renamed or deleted. Pick another from the list." }));
    return;
  }
  showMessage();
  $("#diagram-title").textContent = detail.title;
  document.title = `${detail.title} · mekhen`;
  renderHeads(detail);
  renderSummary(detail);
  for (const role of ROLES) await renderPanel(role, token);
  if (token !== state.loadToken) return;
  if (!viewsFor(id).fitted) fit();
  else applyAll();
}

// -- sidebar -------------------------------------------------------------------------------------

function counts(changes) {
  if (!changes) return "";
  const parts = [];
  if (changes.added) parts.push(`+${changes.added}`);
  if (changes.removed) parts.push(`−${changes.removed}`);
  if (changes.changed) parts.push(`~${changes.changed}`);
  return parts.join(" ");
}

function filteredList() {
  const q = $("#filter").value.trim().toLowerCase();
  return state.list.filter((d) => !q || d.id.toLowerCase().includes(q));
}

function renderList() {
  const ul = $("#diagram-list");
  const items = filteredList();
  if (!state.list.length) {
    ul.replaceChildren(el("li", { class: "list-note", text: "No diagrams yet" }));
    return;
  }
  if (!items.length) {
    ul.replaceChildren(el("li", { class: "list-note", text: "No match" }));
    return;
  }
  ul.replaceChildren(
    ...items.map((d) =>
      el(
        "li",
        {},
        el(
          "a",
          { href: `#${encodeURIComponent(d.id)}`, class: "item", "aria-current": d.id === state.current ? "true" : null, title: d.id },
          el("span", { class: "name", text: d.id }),
          el("span", { class: `status s-${d.status}`, text: STATUS_LABELS[d.status] || d.status }),
          el("span", { class: "counts", text: counts(d.changes) }),
        ),
      ),
    ),
  );
}

async function refreshList() {
  const data = await host.listDiagrams();
  state.list = data ? data.diagrams : [];
  renderList();
}

function idFromHash() {
  const raw = location.hash.slice(1);
  if (!raw) return null;
  try {
    return decodeURIComponent(raw);
  } catch {
    return raw;
  }
}

async function select(id, { replace = false } = {}) {
  if (id && `#${encodeURIComponent(id)}` !== location.hash) {
    const url = `${location.pathname}${location.search}#${encodeURIComponent(id)}`;
    if (replace) history.replaceState(null, "", url);
    else history.pushState(null, "", url);
  }
  state.current = id;
  renderList();
  await loadCurrent();
}

function step(delta) {
  const items = filteredList();
  if (!items.length) return;
  const index = items.findIndex((d) => d.id === state.current);
  const next = items[Math.min(items.length - 1, Math.max(0, index + delta))];
  if (next && next.id !== state.current) select(next.id);
}

// -- toolbar ---------------------------------------------------------------------------------------

function savePrefs() {
  prefs.save({ view: state.view, highlight: state.highlight, sync: state.sync });
}

function setView(view) {
  state.view = view;
  $("#panels").dataset.view = view;
  for (const button of document.querySelectorAll(".segmented button")) {
    button.setAttribute("aria-pressed", String(button.dataset.view === view));
  }
  const params = new URLSearchParams(location.search);
  if (view === "side") params.delete("view");
  else params.set("view", view);
  const query = params.toString();
  history.replaceState(null, "", `${location.pathname}${query ? `?${query}` : ""}${location.hash}`);
  savePrefs();
  requestAnimationFrame(fit);
}

function setSync(on) {
  if (state.current) {
    const views = viewsFor(state.current);
    if (on) Object.assign(views.shared, views[visibleRoles()[0]]);
    else for (const role of ROLES) Object.assign(views[role], views.shared);
  }
  state.sync = on;
  $("#sync").checked = on;
  savePrefs();
  applyAll();
}

async function setHighlight(on) {
  state.highlight = on;
  $("#highlight").checked = on;
  savePrefs();
  const token = state.loadToken;
  for (const role of ROLES) await renderPanel(role, token);
  applyAll();
}

function setConnection(status) {
  const conn = $("#conn");
  conn.dataset.state = status;
  conn.textContent = { live: "live", offline: "reconnecting…", connecting: "connecting" }[status];
  conn.title = status === "live" ? "Diagrams re-render as soon as their files are saved" : "Lost contact with mekhen serve";
}

for (const button of document.querySelectorAll(".segmented button")) {
  button.addEventListener("click", () => setView(button.dataset.view));
}
$("#highlight").addEventListener("change", (e) => setHighlight(e.target.checked));
$("#sync").addEventListener("change", (e) => setSync(e.target.checked));
$("#fit").addEventListener("click", fit);
$("#actual").addEventListener("click", actualSize);
$("#zoom-in").addEventListener("click", () => zoomBy(1.25));
$("#zoom-out").addEventListener("click", () => zoomBy(0.8));
$("#filter").addEventListener("input", renderList);
$("#filter").addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    e.target.value = "";
    renderList();
    e.target.blur();
  } else if (e.key === "Enter") {
    const first = filteredList()[0];
    if (first) select(first.id);
    e.target.blur();
  }
});
window.addEventListener("hashchange", () => {
  const id = idFromHash();
  if (id !== state.current) select(id, { replace: true });
});
document.addEventListener("keydown", (e) => {
  if (e.target instanceof HTMLInputElement || e.ctrlKey || e.metaKey || e.altKey) return;
  const actions = {
    "/": () => $("#filter").focus(),
    1: () => setView("side"),
    2: () => setView("baseline"),
    3: () => setView("proposed"),
    f: fit,
    0: actualSize,
    "+": () => zoomBy(1.25),
    "=": () => zoomBy(1.25),
    "-": () => zoomBy(0.8),
    h: () => setHighlight(!state.highlight),
    s: () => setSync(!state.sync),
    j: () => step(1),
    k: () => step(-1),
    ArrowDown: () => step(1),
    ArrowUp: () => step(-1),
  };
  const action = actions[e.key];
  if (action) {
    e.preventDefault();
    action();
  }
});

// -- start -------------------------------------------------------------------------------------------

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const script = el("script", { src });
    script.addEventListener("load", resolve);
    script.addEventListener("error", () => reject(new Error(`could not load ${src}`)));
    document.head.append(script);
  });
}

async function ensureMermaid(project) {
  if (window.mermaid) return window.mermaid;
  if (project.offlineMermaid) {
    try {
      await loadScript("/vendor/mermaid.min.js");
    } catch {
      /* reported below */
    }
    if (window.mermaid) return window.mermaid;
  }
  throw new Error(
    `Mermaid ${project.mermaidVersion} could not be loaded from the CDN. Check the connection, or run ` +
      "`mekhen mermaid download` once and then `mekhen serve --offline`.",
  );
}

async function start() {
  setView(state.view);
  $("#highlight").checked = state.highlight;
  $("#sync").checked = state.sync;
  try {
    state.project = await host.getProject();
  } catch (error) {
    showMessage(el("h2", { text: "Cannot reach mekhen serve" }), el("p", { text: String(error) }));
    return;
  }
  $("#project-name").textContent = state.project.name;
  $("#project-name").title = state.project.root;
  $("#store-info").textContent = `Store: ${state.project.store.description}`;
  $("#store-info").title = state.project.store.path;

  let mermaid;
  try {
    mermaid = await ensureMermaid(state.project);
  } catch (error) {
    showMessage(el("h2", { text: "Mermaid did not load" }), el("p", { text: error.message }));
    return;
  }
  const dark = window.matchMedia("(prefers-color-scheme: dark)");
  initMermaid(mermaid, dark.matches);
  dark.addEventListener("change", async () => {
    initMermaid(mermaid, dark.matches);
    state.rendered = { baseline: null, proposed: null };
    await loadCurrent();
  });

  await refreshList();
  const wanted = idFromHash();
  const initial = wanted || (state.list[0] && state.list[0].id) || null;
  await select(initial, { replace: true });

  host.subscribe(async (event) => {
    if (event.type === "connected") {
      setConnection("live");
      if (state.everConnected) {
        await refreshList(); // the server may have restarted or files changed while away
        await loadCurrent();
      }
      state.everConnected = true;
    } else if (event.type === "disconnected") {
      setConnection("offline");
    } else if (event.type === "diagrams") {
      await refreshList();
      const touched = [...event.changed, ...event.added, ...event.removed];
      if (!state.current && state.list.length) await select(state.list[0].id, { replace: true });
      else if (state.current && touched.includes(state.current)) await loadCurrent();
    }
  });
}

start();
