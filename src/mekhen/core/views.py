"""The views mekhen builds from code rather than from diagram files:

    @changes   the graph at HEAD vs the working tree; edited files are touched
    @plan      the graph at HEAD with the active plan applied (touch-map + overlay)

Both share the HEAD baseline, so "intended" and "actual" can be compared directly. Without git
(or before the first commit) the working tree is the baseline and @changes is not offered.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from mekhen.core import graph as G
from mekhen.core.plans import ActivePlan, PlanIssue, active_plan, apply_plan
from mekhen.core.providers import PROVIDERS, Provider, ProviderError, detect
from mekhen.core.sources import GitRevision, SourceError, WorkingTree, changed_paths, head_commit
from mekhen.core.discovery import read_text

if TYPE_CHECKING:
    from mekhen.core.project import Project

PLAN_ID = "@plan"
CHANGES_ID = "@changes"
VIRTUAL_IDS = (PLAN_ID, CHANGES_ID)
DEFAULT_MAX_NODES = 60


@dataclass
class View:
    id: str
    title: str
    subtitle: str
    baseline_name: str
    proposed_name: str
    graph: G.Graph | None = None  # the unified graph, focused
    error: str | None = None
    issues: list[PlanIssue] = field(default_factory=list)
    plan: ActivePlan | None = None
    progress: dict | None = None  # @plan only, with git: planned changes that are done (see progress())


# A planned change is done when the working tree shows a matching change to the same node.
DONE_WHEN = {
    "added": {"added", "touched", "changed"},  # the module exists now
    "removed": {"removed"},
    "touched": {"touched", "changed", "added"},
    "changed": {"touched", "changed", "added"},
}


def progress(planned: G.Graph, actual: G.Graph) -> dict:
    """Planned vs done, node by node, from the unfocused @plan and @changes graphs (same baseline)."""
    items = []
    for n in sorted(planned.nodes.values(), key=lambda n: n.id):
        if n.state not in DONE_WHEN:
            continue
        now = actual.nodes.get(n.id)
        items.append({"id": n.id, "label": n.label, "planned": n.state,
                      "done": now is not None and now.state in DONE_WHEN[n.state]})
    planned_ids = {item["id"] for item in items}
    unplanned = [{"id": n.id, "label": n.label, "state": n.state}
                 for n in sorted(actual.nodes.values(), key=lambda n: n.id)
                 if n.state in G.MARKED and n.id not in planned_ids]
    return {"done": sum(item["done"] for item in items), "total": len(items), "items": items,
            "unplanned": unplanned}


class Views:
    def __init__(self, project: Project) -> None:
        self.project = project
        self._lock = threading.RLock()
        self._worktree: WorkingTree | None = None
        self._cache: dict[str, tuple[Any, G.Graph]] = {}  # source kind -> (key, graph): one entry each

    # -- configuration ----------------------------------------------------------------------------

    def worktree(self) -> WorkingTree:
        if self._worktree is None:
            self._worktree = WorkingTree(self.project.root)
        return self._worktree

    def head(self) -> GitRevision | None:
        try:
            return GitRevision(self.project.root) if head_commit(self.project.root) else None
        except SourceError:
            return None

    def provider(self, files: list[str]) -> tuple[Provider, dict]:
        cfg = self.project.cfg
        options_by_name = cfg.get("providers") or {}
        name = cfg.get("provider") or "auto"
        if name == "auto":
            name = detect(files, self._options("python", options_by_name))
        if name not in PROVIDERS:
            raise ProviderError(f"unknown provider {name!r}; choose one of {', '.join(PROVIDERS)} or auto")
        return PROVIDERS[name], self._options(name, options_by_name)

    def _options(self, name: str, options_by_name: dict) -> dict:
        options = dict(options_by_name.get(name) or {})
        if name == "python":  # [gen.imports] from 0.1 configs still applies
            options = {**(self.project.cfg.gen.get("imports") or {}), **options}
        return options

    def max_nodes(self) -> int:
        try:
            return int(self.project.cfg.get("max_nodes", DEFAULT_MAX_NODES))
        except (TypeError, ValueError):
            return DEFAULT_MAX_NODES

    # -- graphs -----------------------------------------------------------------------------------

    def graph(self, source) -> tuple[G.Graph, list[str], Provider, dict]:
        files = source.files()
        provider, options = self.provider(files)
        key = (provider.name, repr(sorted(options.items())), hash(tuple(files)),
               source.key(provider.inputs(files, options)))
        with self._lock:
            cached = self._cache.get(source.kind)
            if cached is None or cached[0] != key:
                cached = (key, provider.build(source, options))
                self._cache[source.kind] = cached
        return cached[1], files, provider, options

    def baseline(self) -> tuple[G.Graph, str, list[str]]:
        head = self.head()
        if head is not None:
            g, files, _, _ = self.graph(head)
            return g, f"HEAD {head.commit[:7]}", files
        g, files, _, _ = self.graph(self.worktree())
        return g, "working tree", files

    def _changes_graph(self, head: GitRevision) -> G.Graph:
        """HEAD vs the working tree, unfocused: edited files touched, new ones added."""
        base, _, _, _ = self.graph(head)
        prop, _, _, _ = self.graph(self.worktree())
        g = G.diff_graphs(base, prop)
        index = {**base.path_index(), **prop.path_index()}
        G.mark_touched(g, {index[p] for p in changed_paths(self.project.root) if p in index})
        return g

    def changes(self) -> View:
        view = View(CHANGES_ID, "Changes", "HEAD vs working tree", "HEAD", "working tree")
        try:
            head = self.head()
            if head is None:
                view.error = "no git history: @changes compares the working tree with the last commit"
                return view
            view.baseline_name = f"HEAD {head.commit[:7]}"
            view.graph = G.focus(self._changes_graph(head), self.max_nodes())
        except (ProviderError, SourceError) as e:
            view.error = str(e)
        return view

    def plan(self) -> View | None:
        active = active_plan(self.project.root)
        if active is None:
            return None
        view = View(PLAN_ID, active.title, "plan", "baseline", "with the plan", plan=active)
        try:
            text = read_text(active.path)
            base, base_name, _ = self.baseline()
            files = self.worktree().files()
            provider, options = self.provider(files)
            view.baseline_name = base_name
            applied = apply_plan(base, text, files, provider, options, self.project.root)
            g = G.diff_graphs(base, applied.graph)
            G.mark_touched(g, applied.touched)
            view.issues = applied.issues
            head = self.head()
            if head is not None:  # both views share the HEAD baseline, so node ids line up
                view.progress = progress(g, self._changes_graph(head))
            view.graph = G.focus(g, self.max_nodes())
        except (ProviderError, SourceError, OSError) as e:
            view.error = str(e)
        return view

    def get(self, view_id: str) -> View | None:
        if view_id == CHANGES_ID:
            return self.changes() if head_commit(self.project.root) else None
        if view_id == PLAN_ID:
            return self.plan()
        return None

    def available(self) -> list[View]:
        out = []
        plan = self.plan()
        if plan is not None:
            out.append(plan)
        if head_commit(self.project.root):
            out.append(self.changes())
        return out

    # -- change detection -------------------------------------------------------------------------

    def plan_signature(self) -> tuple | None:
        """Cheap (two small file reads): changes when the active plan or its text changes."""
        active = active_plan(self.project.root)
        if active is None:
            return None
        try:
            st = active.path.stat()
            return (str(active.path), st.st_mtime_ns, st.st_size, active.time)
        except OSError:
            return (str(active.path), None)

    def code_signature(self) -> tuple:
        """Costlier (git calls, file stats): changes with HEAD, the file list or the provider's inputs."""
        try:
            wt = self.worktree()
            files = wt.files()
            provider, options = self.provider(files)
            return (head_commit(self.project.root), hash(tuple(files)), wt.key(provider.inputs(files, options)))
        except (ProviderError, SourceError) as e:
            return ("error", str(e))


# -- JSON (the same shapes the CLI and the server use for diagram files) ------------------------------


def legacy_diff(g: G.Graph) -> dict:
    """The diff in the shape the viewer's change list and `mekhen show` already understand."""
    out: dict[str, Any] = {"supported": True, "reason": None, "hints": list(g.hints)}
    nodes: dict[str, list] = {"added": [], "removed": [], "changed": []}
    for n in sorted(g.nodes.values(), key=lambda n: n.id):
        if n.state in ("added", "removed"):
            nodes[n.state].append({"id": n.id, "label": n.label, "note": n.note})
        elif n.state in ("changed", "touched"):
            nodes["changed"].append({"id": n.id, "what": [n.state], "label": n.label,
                                     "before": {"label": n.label}, "after": {"label": n.label}})
    groups: dict[str, list] = {"added": [], "removed": [], "changed": []}
    for x in sorted(g.groups.values(), key=lambda x: x.id):
        if x.state in groups:
            groups[x.state].append({"id": x.id, "title": x.label})
    edges: dict[str, list] = {"added": [], "removed": [], "changed": []}
    for e in sorted(g.edges.values(), key=lambda e: (e.src, e.dst)):
        if e.state in edges:
            edges[e.state].append({"src": e.src, "dst": e.dst, "label": e.label, "arrow": "-.->" if e.weak else "-->"})
    out.update(nodes=nodes, subgraphs=groups, edges=edges)
    c = g.counts()
    out["counts"] = {"added": c["added"], "removed": c["removed"], "changed": c["changed"] + c["touched"],
                     "touched": c["touched"]}
    return out


def view_summary(view: View) -> dict:
    counts = legacy_diff(view.graph)["counts"] if view.graph else None
    return {
        "id": view.id,
        "title": view.title,
        "subtitle": view.subtitle,
        "kind": "graph",
        "status": "lint-error" if view.error else ("plan" if view.id == PLAN_ID else "changes"),
        "diagramType": "flowchart",
        "sides": {},
        "errors": int(bool(view.error)) + len(view.issues),
        "warnings": 0,
        "diffSupported": view.graph is not None,
        "changes": counts,
        "progress": ({"done": view.progress["done"], "total": view.progress["total"]}
                     if view.progress else None),
    }


def view_json(view: View, schema: int) -> dict:
    data = {"schema": schema, **view_summary(view), "baseline": None, "proposed": None, "unified": None,
            "graph": None, "diff": None, "lint": [],
            "plan": ({"path": str(view.plan.path), "source": view.plan.source, "time": view.plan.time}
                     if view.plan else None),
            "progress": view.progress}
    if view.error:
        data["lint"].append({"severity": "error", "code": "view", "message": view.error, "path": None,
                             "line": None, "side": None, "diagram": view.id})
    for issue in view.issues:
        data["lint"].append({"severity": "error", "code": "plan", "message": issue.message,
                             "path": str(view.plan.path) if view.plan else None, "line": issue.line,
                             "side": "proposed", "diagram": view.id})
    g = view.graph
    if g is None:
        data["diffSupported"] = False
        data["diff"] = {"supported": False, "reason": view.error or "nothing to show", "hints": [],
                        "nodes": {}, "edges": {}, "subgraphs": {}, "counts": {}}
        return data

    def side(name: str, role: str | None) -> dict:
        return {"path": name, "origin": "generated", "generator": None, "diagramType": "flowchart", "mtime": None,
                "text": G.to_mermaid(g, side=role, highlight=False), "render": G.to_mermaid(g, side=role)}

    data["baseline"] = side(view.baseline_name, "baseline")
    data["proposed"] = side(view.proposed_name, "proposed")
    data["unified"] = side(f"{view.baseline_name} + {view.proposed_name}", None)
    data["graph"] = G.to_json(g)
    data["diff"] = legacy_diff(g)
    return data
