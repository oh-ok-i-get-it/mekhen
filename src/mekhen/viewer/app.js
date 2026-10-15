import { createHost } from "./host.js";
import * as pz from "./panzoom.js";
import { initMermaid } from "./render.js";
import { createRenderer } from "./renderer.js";

const host = createHost();
const renderer = createRenderer();
const ROLES = ["baseline", "proposed", "unified"];
const VIEWS = ["unified", "side", "baseline", "proposed"];
const STATUS_LABELS = {
  both: "both sides",
  "baseline-only": "baseline only",
  "proposed-only": "proposed only",
  "lint-error": "error",
  generated: "generated",
  stale: "stale",
  plan: "plan",
  changes: "HEAD vs working tree",
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
  projects: [],
  follow: null, // {project, time} from the server: the newest active plan
  followOn: saved.follow !== false,
  key: null, // current project key
  project: null, // current project JSON
  list: [],
  current: null, // current diagram id
  detail: null,
  view: VIEWS.includes(urlView) ? urlView : VIEWS.includes(saved.view) ? saved.view : "unified",
  highlight: saved.highlight !== false,
  sync: saved.sync !== false,
  views: new Map(), // "key/id" -> {shared, baseline, proposed, unified, fitted}
  rendered: { baseline: null, proposed: null, unified: null }, // {key, id, ok, w, h}
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

const currentRef = () => (state.key && state.current ? `${state.key}/${state.current}` : null);

// -- which view is in effect -----------------------------------------------------------------------

// "Unified" needs a diagram built from a graph; hand-written pairs fall back to side by side.
function effectiveView() {
  if (state.view === "unified" && !(state.detail && state.detail.unified)) return "side";
  return state.view;
}

function visibleRoles() {
  const view = effectiveView();
  if (view === "side") return ["baseline", "proposed"];
  return [view];
}

// -- views (pan / zoom) ------------------------------------------------------------------------

function viewsFor(ref) {
  if (!state.views.has(ref)) {
    state.views.set(ref, {
      shared: pz.newView(), baseline: pz.newView(), proposed: pz.newView(), unified: pz.newView(), fitted: false,
    });
  }
  return state.views.get(ref);
}

function viewFor(role) {
  const ref = currentRef();
  if (!ref) return null;
  const views = viewsFor(ref);
  return state.sync ? views.shared : views[role];
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
  if (!r || r.id !== currentRef()) return null;
  return { w: r.w, h: r.h, vw: vp.clientWidth, vh: vp.clientHeight };
}

function fit() {
  const ref = currentRef();
  if (!ref) return;
  const views = viewsFor(ref);
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
  const ref = currentRef();
  if (!side) {
    panel.canvas.replaceChildren();
    panel.error.hidden = true;
    panel.empty.hidden = role === "unified";
    if (role !== "unified") {
      const name = state.current;
      const store = state.project ? state.project.store.description : "the store";
      panel.empty.replaceChildren(
        el("p", { class: "empty-title", text: `No ${role} file for ${name}.` }),
        el("p", {}, "Create it with ", el("code", { text: `mekhen new ${name}` }), ` (only missing files are created), in ${store}.`),
      );
    }
    state.rendered[role] = null;
    return;
  }
  panel.empty.hidden = true;
  const source = renderer.key(state.detail, role, state.highlight);
  const key = `${ref}\u0000${source}`;
  const previous = state.rendered[role];
  if (previous && previous.key === key) return;

  const result = await renderer.draw(state.detail, role, state.highlight);
  if (token !== state.loadToken) return; // a newer load replaced this one while the renderer worked

  if (result.svg) {
    panel.canvas.innerHTML = result.svg; // Mermaid output, sanitised by securityLevel "strict"
    panel.canvas.classList.remove("stale");
    panel.error.hidden = true;
    const svg = panel.canvas.querySelector("svg");
    const size = svg ? sizeSvg(svg) : { w: 0, h: 0 };
    state.rendered[role] = { key, id: ref, ok: true, ...size };
    return;
  }

  panel.error.hidden = false;
  panel.error.replaceChildren(
    el("strong", { text: "Mermaid could not render this file" }),
    el("pre", { text: result.error }),
  );
  if (previous && previous.ok && previous.id === ref) {
    panel.canvas.classList.add("stale"); // keep the last good picture, dimmed
    state.rendered[role] = { ...previous, key };
  } else {
    const pre = el("pre", { class: "source", text: numbered(side.text) });
    panel.canvas.replaceChildren(pre);
    panel.canvas.classList.remove("stale");
    state.rendered[role] = { key, id: ref, ok: false, w: pre.scrollWidth, h: pre.scrollHeight };
  }
}

async function renderVisible(token) {
  $("#panels").dataset.view = effectiveView();
  for (const button of document.querySelectorAll(".segmented button")) {
    button.setAttribute("aria-pressed", String(button.dataset.view === effectiveView()));
    if (button.dataset.view === "unified") button.disabled = !(state.detail && state.detail.unified);
  }
  // Only what is on screen; a hidden panel renders when it is shown (its cache key won't match).
  for (const role of visibleRoles()) await renderPanel(role, token);
}

function renderHeads(detail) {
  for (const role of ROLES) {
    const side = detail && detail[role];
    const panel = panels[role];
    // Left-to-right marks keep a leading "." in place inside the right-to-left truncating box.
    panel.path.textContent = side ? `‎${side.path}‎` : "";
    panel.path.title = side ? side.path : "";
    if (side && role === "baseline" && side.origin !== "unknown" && detail.kind !== "graph") {
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
    const informative = label && label !== item.id && label !== "__init__" && !item.id.endsWith(`.${label}`);
    text = informative ? `${item.id} "${label}"` : item.id;
  }
  if (item.note) text += ` (${item.note})`;
  if (item.what && item.what.includes("touched")) {
    text += " — edited";
  } else if (item.what) {
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
  if (detail.kind !== "graph" && (!detail.baseline || !detail.proposed)) {
    body.append(el("p", { class: "note", text: "Only one side exists, so there is nothing to compare yet." }));
  } else if (diff && !diff.supported) {
    body.append(el("p", { class: "note", text: `No change highlighting: ${diff.reason}.` }));
  } else if (diff) {
    const c = diff.counts;
    counts.textContent = `+${c.added} −${c.removed} ~${c.changed}`;
    const list = el("ul", { class: "changes" });
    for (const s of ["added", "removed", "changed"]) {
      for (const item of diff.nodes[s]) list.append(changeLine(s, detail.kind === "graph" ? "module" : "node", item));
      for (const item of diff.subgraphs[s]) list.append(changeLine(s, detail.kind === "graph" ? "group" : "subgraph", item));
      for (const item of diff.edges[s]) list.append(changeLine(s, "edge", item));
    }
    const other = (detail.graph && detail.graph.otherFiles) || [];
    for (const item of other) {
      list.append(el("li", { class: "change other" }, el("span", { class: "sign", text: "·" }), el("span", { class: "kind", text: "file" }), el("span", { text: `${item.path} — ${item.reason}` })));
    }
    if (list.children.length) body.append(list);
    else body.append(el("p", { class: "note", text: detail.kind === "graph" ? "Nothing in the diagram changes." : "No differences between baseline and proposed." }));
    for (const hint of diff.hints) body.append(el("p", { class: "hint", text: `Hint: ${hint}` }));
  }
  if (detail.progress) renderProgress(body, detail.progress);
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

// Planned vs done: each planned change checked against the working tree (HEAD vs now).
function renderProgress(body, progress) {
  const word = { added: "new", removed: "remove", touched: "edit", changed: "edit" };
  body.append(el("h3", { class: "progress-title", text: `Progress: ${progress.done} of ${progress.total} done` }));
  if (progress.items.length) {
    body.append(
      el(
        "ul",
        { class: "progress" },
        progress.items.map((item) =>
          el(
            "li",
            { class: item.done ? "done" : "todo" },
            el("span", { class: "mark", text: item.done ? "✓" : "○" }),
            el("span", { class: "kind", text: word[item.planned] || item.planned }),
            el("span", { text: item.id }),
          ),
        ),
      ),
    );
  }
  if (progress.unplanned.length) {
    body.append(el("p", { class: "note", text: "Changed, but not in the plan:" }));
    body.append(
      el(
        "ul",
        { class: "progress" },
        progress.unplanned.map((item) =>
          el("li", { class: "unplanned" }, el("span", { class: "mark", text: "~" }), el("span", { class: "kind", text: item.state }), el("span", { text: item.id })),
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
  if (!state.key) {
    state.detail = null;
    renderHeads(null);
    renderSummary(null);
    $("#diagram-title").textContent = " ";
    showMessage(
      el("h2", { text: "No projects yet" }),
      el("p", {}, "Run ", el("code", { text: "mekhen serve" }), " in a project, or show a plan with ", el("code", { text: "mekhen plan FILE" }), "."),
    );
    return;
  }
  if (!id) {
    state.detail = null;
    renderHeads(null);
    renderSummary(null);
    $("#diagram-title").textContent = " ";
    if (!state.list.length) {
      const store = state.project ? state.project.store.description : "";
      showMessage(
        el("h2", { text: "Nothing to show yet" }),
        el("p", {}, "Show a plan with ", el("code", { text: "mekhen plan FILE" }), ", commit to see changes against HEAD, or create a diagram pair with ", el("code", { text: "mekhen new NAME" }), "."),
        el("p", { class: "muted", text: `Store: ${store}. New diagrams appear here as soon as they are saved.` }),
      );
    }
    return;
  }
  let detail;
  try {
    detail = await host.getDiagram(state.key, id);
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
  await renderVisible(token);
  if (token !== state.loadToken) return;
  if (!viewsFor(currentRef()).fitted) fit();
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
  return state.list.filter((d) => !q || d.id.toLowerCase().includes(q) || (d.title || "").toLowerCase().includes(q));
}

function itemName(d) {
  if (d.id === "@plan") return `Plan: ${d.title}`;
  if (d.id === "@changes") return "Changes";
  return d.id;
}

function renderList() {
  const ul = $("#diagram-list");
  const items = filteredList();
  if (!state.list.length) {
    ul.replaceChildren(el("li", { class: "list-note", text: "Nothing to show yet" }));
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
          { href: `#${encodeURIComponent(state.key)}/${encodeURIComponent(d.id)}`, class: "item", "aria-current": d.id === state.current ? "true" : null, title: d.id },
          el("span", { class: "name", text: itemName(d) }),
          el("span", { class: `status s-${d.status}`, text: STATUS_LABELS[d.status] || d.status }),
          el("span", { class: "counts", text: d.progress ? `${d.progress.done}/${d.progress.total}` : counts(d.changes), title: d.progress ? "planned changes done" : null }),
        ),
      ),
    ),
  );
}

function renderProjects() {
  const select = $("#project-select");
  select.replaceChildren(
    ...state.projects.map((p) => el("option", { value: p.key, title: p.root, selected: p.key === state.key }, `${p.name}${p.plan ? " · plan" : ""}`)),
  );
  select.disabled = state.projects.length < 2;
  const current = state.projects.find((p) => p.key === state.key);
  select.title = current ? current.root : "";
}

async function refreshProjects() {
  const data = await host.getProjects();
  state.projects = data ? data.projects : [];
  state.follow = data ? data.follow : null;
  renderProjects();
}

async function refreshList() {
  const data = state.key ? await host.listDiagrams(state.key) : null;
  state.list = data ? data.diagrams : [];
  renderList();
}

function parseHash() {
  const raw = location.hash.slice(1);
  if (!raw) return { key: null, id: null };
  const [key, ...rest] = raw.split("/");
  const decode = (s) => {
    try {
      return decodeURIComponent(s);
    } catch {
      return s;
    }
  };
  return { key: decode(key), id: rest.length ? decode(rest.join("/")) : null };
}

function setHash(replace) {
  const hash = state.key ? `#${encodeURIComponent(state.key)}${state.current ? `/${encodeURIComponent(state.current)}` : ""}` : "";
  if (hash === location.hash) return;
  const url = `${location.pathname}${location.search}${hash}`;
  if (replace) history.replaceState(null, "", url);
  else history.pushState(null, "", url);
}

// Switch project and/or diagram. `id` undefined: keep the current one if the project has it.
async function select(key, id, { replace = false } = {}) {
  if (key !== state.key) {
    state.key = key;
    state.project = key ? await host.getProject(key) : null;
    $("#store-info").textContent = state.project ? `Store: ${state.project.store.description}` : "";
    $("#store-info").title = state.project ? state.project.store.path : "";
    renderProjects();
    await refreshList();
  }
  const ids = state.list.map((d) => d.id);
  if (!ids.includes(id)) {
    const keep = id === undefined && ids.includes(state.current);
    id = keep ? state.current : (ids[0] ?? null);
  }
  state.current = id;
  setHash(replace);
  renderList();
  await loadCurrent();
}

function step(delta) {
  const items = filteredList();
  if (!items.length) return;
  const index = items.findIndex((d) => d.id === state.current);
  const next = items[Math.min(items.length - 1, Math.max(0, index + delta))];
  if (next && next.id !== state.current) select(state.key, next.id);
}

async function followIfNeeded(previous) {
  const target = state.follow;
  if (!state.followOn || !target) return;
  const isNew = !previous || previous.project !== target.project || previous.time !== target.time;
  if (isNew && (target.project !== state.key || state.current !== "@plan")) await select(target.project, "@plan");
}

// -- toolbar ---------------------------------------------------------------------------------------

function savePrefs() {
  prefs.save({ view: state.view, highlight: state.highlight, sync: state.sync, follow: state.followOn });
}

async function setView(view) {
  state.view = view;
  const params = new URLSearchParams(location.search);
  if (view === "unified") params.delete("view");
  else params.set("view", view);
  const query = params.toString();
  history.replaceState(null, "", `${location.pathname}${query ? `?${query}` : ""}${location.hash}`);
  savePrefs();
  await renderVisible(state.loadToken);
  requestAnimationFrame(fit);
}

function setSync(on) {
  const ref = currentRef();
  if (ref) {
    const views = viewsFor(ref);
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
  await renderVisible(state.loadToken);
  applyAll();
}

async function setFollow(on) {
  state.followOn = on;
  $("#follow").checked = on;
  savePrefs();
  if (on) await followIfNeeded(null);
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
$("#follow").addEventListener("change", (e) => setFollow(e.target.checked));
$("#project-select").addEventListener("change", (e) => select(e.target.value, undefined));
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
    if (first) select(state.key, first.id);
    e.target.blur();
  }
});
window.addEventListener("hashchange", () => {
  const { key, id } = parseHash();
  if (key !== state.key || id !== state.current) select(key || state.key, id, { replace: true });
});
document.addEventListener("keydown", (e) => {
  if (e.target instanceof HTMLInputElement || e.target instanceof HTMLSelectElement || e.ctrlKey || e.metaKey || e.altKey) return;
  const actions = {
    "/": () => $("#filter").focus(),
    1: () => setView("unified"),
    2: () => setView("side"),
    3: () => setView("baseline"),
    4: () => setView("proposed"),
    f: fit,
    0: actualSize,
    "+": () => zoomBy(1.25),
    "=": () => zoomBy(1.25),
    "-": () => zoomBy(0.8),
    h: () => setHighlight(!state.highlight),
    s: () => setSync(!state.sync),
    p: () => setFollow(!state.followOn),
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
  if (project && project.offlineMermaid) {
    try {
      await loadScript("/vendor/mermaid.min.js");
    } catch {
      /* reported below */
    }
    if (window.mermaid) return window.mermaid;
  }
  throw new Error(
    `Mermaid ${project ? project.mermaidVersion : ""} could not be loaded from the CDN. Check the connection, or run ` +
      "`mekhen mermaid download` once and then `mekhen serve --offline`.",
  );
}

async function start() {
  $("#highlight").checked = state.highlight;
  $("#sync").checked = state.sync;
  $("#follow").checked = state.followOn;
  try {
    await refreshProjects();
  } catch (error) {
    showMessage(el("h2", { text: "Cannot reach mekhen serve" }), el("p", { text: String(error) }));
    return;
  }
  const wanted = parseHash();
  const known = (key) => state.projects.some((p) => p.key === key);
  let key = known(wanted.key) ? wanted.key : null;
  let id = key ? wanted.id : null;
  if (!key && state.followOn && state.follow && known(state.follow.project)) [key, id] = [state.follow.project, "@plan"];
  if (!key && state.projects.length) key = state.projects[0].key;

  const project = key ? await host.getProject(key) : null;
  let mermaid;
  try {
    mermaid = await ensureMermaid(project);
  } catch (error) {
    showMessage(el("h2", { text: "Mermaid did not load" }), el("p", { text: error.message }));
    return;
  }
  const dark = window.matchMedia("(prefers-color-scheme: dark)");
  initMermaid(mermaid, dark.matches);
  dark.addEventListener("change", async () => {
    initMermaid(mermaid, dark.matches);
    state.rendered = { baseline: null, proposed: null, unified: null };
    await loadCurrent();
  });

  await select(key, id, { replace: true });

  host.subscribe(async (event) => {
    if (event.type === "connected") {
      setConnection("live");
      if (state.everConnected) {
        await refreshProjects(); // the server may have restarted or files changed while away
        await refreshList();
        await loadCurrent();
      }
      state.everConnected = true;
    } else if (event.type === "disconnected") {
      setConnection("offline");
    } else if (event.type === "projects") {
      const previous = state.follow;
      state.projects = event.projects;
      state.follow = event.follow;
      renderProjects();
      if (!state.key && state.projects.length) await select(state.projects[0].key, undefined, { replace: true });
      await followIfNeeded(previous);
      if (state.key) await refreshList();
    } else if (event.type === "diagrams" && event.project === state.key) {
      await refreshList();
      const touched = [...event.changed, ...event.added, ...event.removed];
      if (!state.current && state.list.length) await select(state.key, state.list[0].id, { replace: true });
      else if (state.current && touched.includes(state.current)) await loadCurrent();
    }
  });
}

start();
