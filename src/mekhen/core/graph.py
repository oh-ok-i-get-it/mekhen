"""The graph contract: what every provider produces and every renderer consumes (schema v1).

A graph has groups (folders, packages), nodes (modules, files) and edges (dependencies). Node ids
and group ids are separate namespaces. Every element carries a `state`:

    same      unchanged
    added     only in the proposal (green)
    removed   only in the baseline (red, dashed)
    changed   in both, with a different label or group (amber)
    touched   in both and unchanged in structure, but its file is edited (amber)

The JSON form (`to_json`) is documented in docs/graph-schema.md and is the format the `command`
provider reads from external tools. Mermaid is one rendering of it (`to_mermaid`).
"""

from __future__ import annotations

import copy as _copy
import re
from dataclasses import dataclass, field

SCHEMA = 1
STATES = ("same", "added", "removed", "changed", "touched")
MARKED = {"added", "removed", "changed", "touched"}


@dataclass
class Node:
    id: str
    label: str
    group: str | None = None
    paths: list[str] = field(default_factory=list)  # files this node stands for (several after collapsing)
    state: str = "same"
    note: str | None = None  # e.g. "moved from x" or "collapsed: 12 modules"


@dataclass
class Group:
    id: str
    label: str
    parent: str | None = None
    state: str = "same"
    path: str | None = None  # the folder this group stands for, when it is one


@dataclass
class Edge:
    src: str
    dst: str
    weak: bool = False  # drawn dotted, e.g. an import only used for type checking
    state: str = "same"
    label: str | None = None


@dataclass
class Graph:
    nodes: dict[str, Node] = field(default_factory=dict)
    groups: dict[str, Group] = field(default_factory=dict)
    edges: dict[tuple[str, str], Edge] = field(default_factory=dict)
    hints: list[str] = field(default_factory=list)
    other_files: list[dict] = field(default_factory=list)  # files a plan names that aren't in the graph
    direction: str = "LR"

    # -- building ---------------------------------------------------------------------------------

    def add_group(self, gid: str, label: str, parent: str | None = None, path: str | None = None) -> Group:
        if gid not in self.groups:
            self.groups[gid] = Group(gid, label, parent, path=path)
        return self.groups[gid]

    def add_node(self, nid: str, label: str, group: str | None = None, paths: list[str] | None = None) -> Node:
        node = self.nodes.get(nid)
        if node is None:
            node = self.nodes[nid] = Node(nid, label, group, list(paths or []))
        else:
            node.paths.extend(p for p in paths or [] if p not in node.paths)
        return node

    def add_edge(self, src: str, dst: str, weak: bool = False, label: str | None = None) -> Edge:
        key = (src, dst)
        edge = self.edges.get(key)
        if edge is None:
            edge = self.edges[key] = Edge(src, dst, weak, label=label)
        else:
            edge.weak = edge.weak and weak  # a real import wins over a type-only one
        return edge

    def copy(self) -> Graph:
        return _copy.deepcopy(self)

    # -- queries ----------------------------------------------------------------------------------

    def path_index(self) -> dict[str, str]:
        """{file path: node id}"""
        return {p: n.id for n in self.nodes.values() for p in n.paths}

    def subtree_groups(self, gid: str) -> set[str]:
        out = {gid}
        changed = True
        while changed:
            changed = False
            for g in self.groups.values():
                if g.parent in out and g.id not in out:
                    out.add(g.id)
                    changed = True
        return out

    def counts(self) -> dict[str, int]:
        out = {s: 0 for s in ("added", "removed", "changed", "touched")}
        for item in [*self.nodes.values(), *self.groups.values(), *self.edges.values()]:
            if item.state in out:
                out[item.state] += 1
        return out

    def is_marked(self) -> bool:
        return any(c for c in self.counts().values())


# -- comparing ------------------------------------------------------------------------------------


def diff_graphs(base: Graph, prop: Graph) -> Graph:
    """One graph holding both: elements only in `prop` are added, only in `base` removed."""
    out = Graph(direction=prop.direction or base.direction, hints=[*base.hints, *prop.hints],
                other_files=[*prop.other_files])
    for gid, g in base.groups.items():
        other = prop.groups.get(gid)
        new = out.groups[gid] = _copy.copy(other or g)
        new.state = "removed" if other is None else ("changed" if (g.label, g.parent) != (other.label, other.parent) else "same")
    for gid, g in prop.groups.items():
        if gid not in base.groups:
            new = out.groups[gid] = _copy.copy(g)
            new.state = "added"
    for nid, n in base.nodes.items():
        other = prop.nodes.get(nid)
        new = out.nodes[nid] = _copy.deepcopy(other or n)
        if other is None:
            new.state = "removed"
        elif (n.label, n.group) != (other.label, other.group):
            new.state = "changed"
        else:
            new.state = other.state if other.state in MARKED else "same"
    for nid, n in prop.nodes.items():
        if nid not in base.nodes:
            new = out.nodes[nid] = _copy.deepcopy(n)
            new.state = "added"
    for key, e in base.edges.items():
        other = prop.edges.get(key)
        new = out.edges[key] = _copy.copy(other or e)
        if other is None:
            new.state = "removed"
        elif (e.weak, e.label) != (other.weak, other.label):
            new.state = "changed"
        else:
            new.state = "same"
    for key, e in prop.edges.items():
        if key not in base.edges:
            new = out.edges[key] = _copy.copy(e)
            new.state = "added"
    _rename_hints(out)
    return out


def _rename_hints(g: Graph) -> None:
    added: dict[str, list[str]] = {}
    for n in g.nodes.values():
        if n.state == "added" and n.label:
            added.setdefault(n.label.lower(), []).append(n.id)
    for n in g.nodes.values():
        if n.state == "removed" and n.label and not n.note:
            match = added.get(n.label.lower(), [])
            if len(match) == 1:
                g.hints.append(f"possibly moved: {n.id} to {match[0]} (same name)")


def mark_touched(g: Graph, node_ids: set[str]) -> None:
    """Mark unchanged nodes whose files are edited."""
    for nid in node_ids:
        node = g.nodes.get(nid)
        if node is not None and node.state == "same":
            node.state = "touched"


# -- focusing -------------------------------------------------------------------------------------


def focus(g: Graph, max_nodes: int) -> Graph:
    """When the graph has more than `max_nodes` nodes, collapse every group with nothing marked
    inside it (and no marked edge touching it) into a single node. Marked parts stay expanded."""
    if max_nodes <= 0 or len(g.nodes) <= max_nodes:
        return g
    marked_groups: set[str] = set()

    def mark_up(gid: str | None) -> None:
        while gid is not None and gid not in marked_groups:
            marked_groups.add(gid)
            gid = g.groups[gid].parent if gid in g.groups else None

    for n in g.nodes.values():
        if n.state in MARKED:
            mark_up(n.group)
    for e in g.edges.values():
        if e.state in MARKED:
            for end in (e.src, e.dst):
                if end in g.nodes:
                    mark_up(g.nodes[end].group)
    for grp in g.groups.values():
        if grp.state in MARKED:
            mark_up(grp.id)

    # Collapse maximal unmarked subtrees: unmarked groups whose parent is marked or absent.
    roots = [grp for grp in g.groups.values()
             if grp.id not in marked_groups and (grp.parent is None or grp.parent in marked_groups)]
    if not roots:
        return g
    out = g.copy()
    owner: dict[str, str] = {}  # node id -> collapsed node id
    for root in roots:
        sub = g.subtree_groups(root.id)
        members = [n for n in g.nodes.values() if n.group in sub]
        cid = f"{root.id}/*"
        for gid in sub:
            out.groups.pop(gid, None)
        for n in members:
            owner[n.id] = cid
            out.nodes.pop(n.id, None)
        out.nodes[cid] = Node(cid, root.label, root.parent, sorted(p for n in members for p in n.paths),
                              note=f"collapsed: {len(members)} item{'s' if len(members) != 1 else ''}")
    edges: dict[tuple[str, str], Edge] = {}
    for (src, dst), e in g.edges.items():
        a, b = owner.get(src, src), owner.get(dst, dst)
        if a == b:
            continue
        key = (a, b)
        if key in edges:
            edges[key].weak = edges[key].weak and e.weak
        else:
            edges[key] = Edge(a, b, e.weak, e.state, e.label)
    out.edges = edges
    return out


# -- output ---------------------------------------------------------------------------------------


def to_json(g: Graph) -> dict:
    return {
        "schema": SCHEMA,
        "direction": g.direction,
        "groups": [{"id": x.id, "label": x.label, "parent": x.parent, "path": x.path, "state": x.state}
                   for x in g.groups.values()],
        "nodes": [{"id": n.id, "label": n.label, "group": n.group, "paths": n.paths, "state": n.state,
                   "note": n.note} for n in g.nodes.values()],
        "edges": [{"src": e.src, "dst": e.dst, "weak": e.weak, "state": e.state, "label": e.label}
                  for e in g.edges.values()],
        "hints": g.hints,
        "otherFiles": g.other_files,
        "counts": g.counts(),
    }


class SchemaError(ValueError):
    pass


def from_json(data: dict) -> Graph:
    """Read a graph from JSON (the `command` provider's output). Validates the essentials."""
    if not isinstance(data, dict):
        raise SchemaError("expected a JSON object")
    schema = data.get("schema", SCHEMA)
    if schema != SCHEMA:
        raise SchemaError(f"unsupported graph schema {schema!r} (mekhen reads schema {SCHEMA})")
    g = Graph(direction=str(data.get("direction") or "LR"))
    for item in data.get("groups") or []:
        if not isinstance(item, dict) or not item.get("id"):
            raise SchemaError(f"group without an id: {item!r}")
        g.add_group(str(item["id"]), str(item.get("label") or item["id"]), item.get("parent"), item.get("path"))
    for item in data.get("nodes") or []:
        if not isinstance(item, dict) or not item.get("id"):
            raise SchemaError(f"node without an id: {item!r}")
        paths = item.get("paths")
        if paths is None and item.get("path"):
            paths = [item["path"]]
        g.add_node(str(item["id"]), str(item.get("label") or item["id"]), item.get("group"),
                   [str(p) for p in paths or []])
    for item in data.get("edges") or []:
        if not isinstance(item, dict) or "src" not in item or "dst" not in item:
            raise SchemaError(f"edge needs src and dst: {item!r}")
        g.add_edge(str(item["src"]), str(item["dst"]), bool(item.get("weak")), item.get("label"))
    for e in g.edges.values():
        for end in (e.src, e.dst):
            if end not in g.nodes:
                raise SchemaError(f"edge {e.src} -> {e.dst} refers to unknown node {end!r}")
    for n in g.nodes.values():
        if n.group is not None and n.group not in g.groups:
            raise SchemaError(f"node {n.id!r} is in unknown group {n.group!r}")
    return g


# -- Mermaid --------------------------------------------------------------------------------------

NODE_STYLES = {
    "added": "fill:#dafbe1,stroke:#1a7f37,stroke-width:3px,color:#0b3d1a",
    "removed": "fill:#ffebe9,stroke:#cf222e,stroke-width:3px,stroke-dasharray:6 3,color:#5a0e12",
    "changed": "fill:#fff8c5,stroke:#bf8700,stroke-width:3px,color:#4d3800",
    "touched": "fill:#fff8c5,stroke:#bf8700,stroke-width:2px,color:#4d3800",
}
EDGE_STYLES = {
    "added": "stroke:#1a7f37,stroke-width:3px",
    "removed": "stroke:#cf222e,stroke-width:3px,stroke-dasharray:6 3",
    "changed": "stroke:#bf8700,stroke-width:3px",
}
FADED_EDGE = "stroke:#8c959f,stroke-opacity:0.35"
CLASS_NAMES = {"added": "mekhenAdded", "removed": "mekhenRemoved", "changed": "mekhenChanged",
               "touched": "mekhenTouched"}
# Which states each side of a side-by-side view shows.
SIDE_HIDES = {"baseline": "added", "proposed": "removed"}


class _Ids:
    """Mermaid-safe ids: `.` becomes `__`, other punctuation `_xNNNN`; clashes get a numeric suffix."""

    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.map: dict[str, str] = {}
        self.used: set[str] = set()

    def __call__(self, raw: str) -> str:
        if raw not in self.map:
            safe = "".join(ch if re.match(r"[A-Za-z0-9_]", ch) else "__" if ch == "." else f"_x{ord(ch):04x}"
                           for ch in raw)
            mid, n = self.prefix + safe, 2
            while mid in self.used:
                mid, n = f"{self.prefix}{safe}_{n}", n + 1
            self.map[raw] = mid
            self.used.add(mid)
        return self.map[raw]


def _label(text: str) -> str:
    """Mermaid entity codes for characters that would end or confuse a quoted label."""
    return text.replace("#", "#35;").replace('"', "#quot;").replace("<", "#lt;").replace(">", "#gt;")


def to_mermaid(g: Graph, *, side: str | None = None, highlight: bool = True, header: str | None = None) -> str:
    """Mermaid flowchart text. `side` = "baseline" / "proposed" drops what that side doesn't have."""
    hidden = SIDE_HIDES.get(side or "")
    nodes = {k: n for k, n in g.nodes.items() if n.state != hidden}
    groups = {k: x for k, x in g.groups.items() if x.state != hidden}
    edges = [e for e in g.edges.values() if e.state != hidden and e.src in nodes and e.dst in nodes]

    def visible_state(state: str) -> str | None:
        return state if highlight and state in MARKED else None

    nid, gid = _Ids("n_"), _Ids("g_")
    lines = [header] if header else []
    lines.append(f"flowchart {g.direction or 'LR'}")
    marks: dict[str, list[str]] = {}

    def emit(parent: str | None, indent: int) -> None:
        pad = "    " * indent
        for n in sorted((n for n in nodes.values() if (n.group if n.group in groups else None) == parent),
                        key=lambda n: n.id):
            label = _label(n.label)
            shape = f'(["{label}"])' if n.note and n.note.startswith("collapsed") else f'["{label}"]'
            lines.append(f"{pad}{nid(n.id)}{shape}")
            if (s := visible_state(n.state)):
                marks.setdefault(s, []).append(nid(n.id))
        for sub in sorted((x for x in groups.values() if (x.parent if x.parent in groups else None) == parent),
                          key=lambda x: x.id):
            lines.append(f'{pad}subgraph {gid(sub.id)}["{_label(sub.label)}"]')
            emit(sub.id, indent + 1)
            lines.append(f"{pad}end")
            if (s := visible_state(sub.state)) and s != "touched":
                marks.setdefault(s, []).append(gid(sub.id))

    emit(None, 1)
    edge_marks: dict[str, list[int]] = {}
    for i, e in enumerate(sorted(edges, key=lambda e: (e.src, e.dst))):
        arrow = "-.->" if e.weak else "-->"
        text = f'|"{_label(e.label)}"|' if e.label else ""
        lines.append(f"    {nid(e.src)} {arrow}{text} {nid(e.dst)}")
        if (s := visible_state(e.state)) and s in EDGE_STYLES:
            edge_marks.setdefault(s, []).append(i)
    if marks or edge_marks:
        lines.append("    %% mekhen: change highlighting")
        if edges:  # fade everything unmarked so the changes stand out; marked edges override below
            lines.append(f"    linkStyle default {FADED_EDGE}")
    for state, ids in marks.items():
        lines.append(f"    classDef {CLASS_NAMES[state]} {NODE_STYLES[state]}")
        lines.append(f"    class {','.join(ids)} {CLASS_NAMES[state]}")
    for state, idx in edge_marks.items():
        lines.append(f"    linkStyle {','.join(map(str, idx))} {EDGE_STYLES[state]}")
    return "\n".join(lines) + "\n"
