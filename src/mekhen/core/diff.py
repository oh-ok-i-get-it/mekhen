"""Baseline vs proposed diff for flowcharts, and the decorated copies the viewer renders.

Matching:
- nodes and subgraphs by id; same id with a different label, shape or parent subgraph = changed
- edges by (source, target, n-th occurrence of that pair); a different label or arrow = changed

Not detected: renamed ids (one removed + one added; a hint is given when labels match),
style-only changes, and anything in non-flowchart diagrams.

Highlighting is applied to a copy of the text by appending `classDef`, `class` and `linkStyle`
lines; source files are never modified.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from mekhen.core.flowchart import Edge, Flowchart

CLASS_NAMES = {"added": "mekhenAdded", "removed": "mekhenRemoved", "changed": "mekhenChanged"}
NODE_STYLES = {
    "added": "fill:#dafbe1,stroke:#1a7f37,stroke-width:3px,color:#0b3d1a",
    "removed": "fill:#ffebe9,stroke:#cf222e,stroke-width:3px,stroke-dasharray:6 3,color:#5a0e12",
    "changed": "fill:#fff8c5,stroke:#bf8700,stroke-width:3px,color:#4d3800",
}
EDGE_STYLES = {
    "added": "stroke:#1a7f37,stroke-width:3px",
    "removed": "stroke:#cf222e,stroke-width:3px,stroke-dasharray:6 3",
    "changed": "stroke:#bf8700,stroke-width:3px",
}
# Which states each side shows: removed things exist only in the baseline, added only in the proposal.
SIDE_STATES = {"baseline": ("removed", "changed"), "proposed": ("added", "changed")}


@dataclass
class DiffResult:
    supported: bool
    reason: str | None = None
    nodes: dict[str, list[dict]] = field(default_factory=lambda: {"added": [], "removed": [], "changed": []})
    edges: dict[str, list[dict]] = field(default_factory=lambda: {"added": [], "removed": [], "changed": []})
    subgraphs: dict[str, list[dict]] = field(default_factory=lambda: {"added": [], "removed": [], "changed": []})
    hints: list[str] = field(default_factory=list)
    # role -> state -> node/subgraph ids, and role -> state -> edge indices
    _mark_ids: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    _mark_edges: dict[str, dict[str, list[int]]] = field(default_factory=dict)

    @property
    def counts(self) -> dict[str, int]:
        return {state: len(self.nodes[state]) + len(self.edges[state]) + len(self.subgraphs[state])
                for state in ("added", "removed", "changed")}

    def to_json(self) -> dict:
        return {
            "supported": self.supported,
            "reason": self.reason,
            "nodes": self.nodes,
            "edges": self.edges,
            "subgraphs": self.subgraphs,
            "hints": self.hints,
            "counts": self.counts,
        }

    def decorate(self, text: str, role: str) -> str:
        """`text` with highlight lines appended for `role` ("baseline" or "proposed")."""
        if not self.supported:
            return text
        ids = self._mark_ids.get(role, {})
        edges = self._mark_edges.get(role, {})
        if not any(ids.values()) and not any(edges.values()):
            return text
        out = [text.rstrip("\n"), "", "%% mekhen: change highlighting (added to the render copy only)"]
        for state in SIDE_STATES[role]:
            if ids.get(state):
                out.append(f"classDef {CLASS_NAMES[state]} {NODE_STYLES[state]}")
                out.append(f"class {','.join(ids[state])} {CLASS_NAMES[state]}")
            if edges.get(state):
                out.append(f"linkStyle {','.join(str(i) for i in edges[state])} {EDGE_STYLES[state]}")
        return "\n".join(out) + "\n"


def _norm(label: str | None) -> str:
    return re.sub(r"\s+", " ", (label or "").strip()).lower()


def _keyed_edges(fc: Flowchart) -> dict[tuple[str, str, int], Edge]:
    seen: dict[tuple[str, str], int] = defaultdict(int)
    out = {}
    for edge in fc.edges:
        n = seen[(edge.src, edge.dst)]
        seen[(edge.src, edge.dst)] += 1
        out[(edge.src, edge.dst, n)] = edge
    return out


def _edge_json(edge: Edge) -> dict:
    return {"src": edge.src, "dst": edge.dst, "label": edge.label, "arrow": edge.arrow}


def unsupported(reason: str) -> DiffResult:
    return DiffResult(supported=False, reason=reason)


def diff(base: Flowchart, prop: Flowchart) -> DiffResult:
    if base.errors or prop.errors:
        which = "baseline" if base.errors else "proposed"
        return unsupported(f"the {which} has syntax errors; fix them to see changes highlighted")

    result = DiffResult(supported=True)
    marks: dict[str, dict[str, list[str]]] = {r: defaultdict(list) for r in SIDE_STATES}
    edge_marks: dict[str, dict[str, list[int]]] = {r: defaultdict(list) for r in SIDE_STATES}

    for nid, node in prop.nodes.items():
        if nid not in base.nodes:
            result.nodes["added"].append({"id": nid, "label": node.label})
            marks["proposed"]["added"].append(nid)
    for nid, node in base.nodes.items():
        other = prop.nodes.get(nid)
        if other is None:
            result.nodes["removed"].append({"id": nid, "label": node.label})
            marks["baseline"]["removed"].append(nid)
            continue
        what = []
        if (node.label or nid) != (other.label or nid):
            what.append("label")
        if node.shape != other.shape and not {node.shape, other.shape} <= {"default", "rect"}:
            what.append("shape")
        if node.parent != other.parent:
            what.append("moved")
        if what:
            result.nodes["changed"].append({
                "id": nid,
                "what": what,
                "before": {"label": node.label, "shape": node.shape, "parent": node.parent},
                "after": {"label": other.label, "shape": other.shape, "parent": other.parent},
            })
            marks["baseline"]["changed"].append(nid)
            marks["proposed"]["changed"].append(nid)

    for sid, sg in prop.subgraphs.items():
        if sid not in base.subgraphs:
            result.subgraphs["added"].append({"id": sid, "title": sg.title})
            marks["proposed"]["added"].append(sid)
    for sid, sg in base.subgraphs.items():
        other = prop.subgraphs.get(sid)
        if other is None:
            result.subgraphs["removed"].append({"id": sid, "title": sg.title})
            marks["baseline"]["removed"].append(sid)
            continue
        what = []
        if sg.title != other.title:
            what.append("title")
        if sg.parent != other.parent:
            what.append("moved")
        if what:
            result.subgraphs["changed"].append({
                "id": sid,
                "what": what,
                "before": {"title": sg.title, "parent": sg.parent},
                "after": {"title": other.title, "parent": other.parent},
            })
            marks["baseline"]["changed"].append(sid)
            marks["proposed"]["changed"].append(sid)

    base_edges, prop_edges = _keyed_edges(base), _keyed_edges(prop)
    for key, edge in prop_edges.items():
        if key not in base_edges:
            result.edges["added"].append(_edge_json(edge))
            edge_marks["proposed"]["added"].append(edge.index)
    for key, edge in base_edges.items():
        other = prop_edges.get(key)
        if other is None:
            result.edges["removed"].append(_edge_json(edge))
            edge_marks["baseline"]["removed"].append(edge.index)
            continue
        what = []
        if (edge.label or "") != (other.label or ""):
            what.append("label")
        if edge.arrow != other.arrow:
            what.append("arrow")
        if what:
            result.edges["changed"].append({
                "src": edge.src, "dst": edge.dst, "what": what,
                "before": {"label": edge.label, "arrow": edge.arrow},
                "after": {"label": other.label, "arrow": other.arrow},
            })
            edge_marks["baseline"]["changed"].append(edge.index)
            edge_marks["proposed"]["changed"].append(other.index)

    # Possible renames: a removed and an added node with the same (non-empty) label.
    added_by_label: dict[str, list[str]] = defaultdict(list)
    for item in result.nodes["added"]:
        if _norm(item["label"]):
            added_by_label[_norm(item["label"])].append(item["id"])
    for item in result.nodes["removed"]:
        candidates = added_by_label.get(_norm(item["label"]), [])
        if len(candidates) == 1:
            result.hints.append(
                f"possibly renamed: {item['id']} to {candidates[0]} (same label). "
                "Keep the old id to have it shown as one node."
            )

    result._mark_ids = {r: dict(v) for r, v in marks.items()}
    result._mark_edges = {r: dict(v) for r, v in edge_marks.items()}
    return result
