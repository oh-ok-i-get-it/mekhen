"""Plans: any Markdown file that describes a change. Nothing here is specific to one tool.

Two things are read from a plan:

- the touch-map: file paths (and dotted module names in `backticks`) the plan mentions. Existing
  files are marked touched; files that don't exist yet are added. This needs nothing from the
  plan's author.
- an optional overlay, for structural changes, in a fenced block:

    ```mekhen
    + src/pkg/plans.py "Plan reader"          new module
    - src/pkg/store.py                        removed module
    src/pkg/old.py => src/pkg/new.py          moved or renamed (keeps its edges)
    + src/pkg/project.py -> src/pkg/plans.py  new dependency
    - src/pkg/a.py -> src/pkg/b.py            removed dependency
    ```

  Names are repo paths (shortened paths work when unambiguous) or, for Python, dotted modules.
  `#` starts a comment.

Which plan is active for a project is kept in ~/.mekhen/state/<project>/active-plan.json, written
by `mekhen plan FILE`, `mekhen serve --plan FILE` or an editor/agent integration.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

from mekhen.core.config import mekhen_home
from mekhen.core.discovery import read_text
from mekhen.core.graph import Graph
from mekhen.core.store import project_key

_OPEN = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_LINK = re.compile(r"\]\(([^)\s]+)\)")
_TICK = re.compile(r"`([^`\n]+)`")
_BARE = re.compile(r"(?<![\w/.\\-])((?:[\w.-]+[/\\])+[\w.-]+\.[A-Za-z0-9]+)(?![\w/])")
_DOTTED = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+$")
_FILE_LIKE = re.compile(r"\.[A-Za-z0-9]{1,8}$")
_SUFFIX = re.compile(r"(#L\d+(-L?\d+)?|:\d+(-\d+)?(:\d+)?)$")
_BRACES = re.compile(r"\{([^{}]*,[^{}]*)\}")

_OPS = [
    ("link", re.compile(r"^\+\s+(\S+)\s*->\s*(\S+)$")),
    ("unlink", re.compile(r"^-\s+(\S+)\s*->\s*(\S+)$")),
    ("add", re.compile(r'^\+\s+(\S+)(?:\s+"([^"]*)")?$')),
    ("drop", re.compile(r"^-\s+(\S+)$")),
    ("move", re.compile(r"^(\S+)\s*=>\s*(\S+)$")),
]
SYNTAX_HELP = 'expected `+ PATH ["label"]`, `- PATH`, `OLD => NEW`, `+ A -> B` or `- A -> B`'


@dataclass
class Op:
    kind: str  # add | drop | move | link | unlink
    a: str
    b: str | None
    label: str | None
    line: int


@dataclass
class PlanIssue:
    line: int
    message: str


# -- reading a plan ---------------------------------------------------------------------------------


@dataclass
class Fence:
    info: str  # the text after the opening backticks, e.g. "mekhen" or "python"
    first: int  # 0-based index of the first line inside the block
    last: int  # 0-based index one past the last line inside the block


def fences(lines: list[str]) -> list[Fence]:
    """Top-level fenced code blocks (``` or ~~~, CommonMark rules): a block shown as an example
    inside a longer fence is not a block of its own."""
    out = []
    i, n = 0, len(lines)
    while i < n:
        m = _OPEN.match(lines[i])
        if not m:
            i += 1
            continue
        fence, info = m.group(1), m.group(2).strip()
        close = re.compile(rf"^ {{0,3}}{re.escape(fence[0])}{{{len(fence)},}}\s*$")
        j = i + 1
        while j < n and not close.match(lines[j]):
            j += 1
        out.append(Fence(info, i + 1, j))
        i = j + 1
    return out


def _overlay_blocks(lines: list[str]) -> list[Fence]:
    return [f for f in fences(lines) if f.info.split()[:1] == ["mekhen"]]


def parse_overlay(text: str) -> tuple[list[Op], list[PlanIssue]]:
    ops: list[Op] = []
    issues: list[PlanIssue] = []
    lines = text.split("\n")
    for block in _overlay_blocks(lines):
        for index in range(block.first, block.last):
            line = re.split(r"(?:^|\s)#", lines[index], maxsplit=1)[0].strip()
            if not line:
                continue
            for kind, pattern in _OPS:
                m = pattern.match(line)
                if m:
                    a, second = (*m.groups(), None)[:2]
                    if kind == "add":
                        ops.append(Op(kind, a, None, second, index + 1))
                    else:
                        ops.append(Op(kind, a, second, None, index + 1))
                    break
            else:
                issues.append(PlanIssue(index + 1, f"cannot read {line!r}: {SYNTAX_HELP}"))
    return ops, issues


def _expand(token: str) -> list[str]:
    """`core/{a,b}.py` -> core/a.py, core/b.py (shell-style braces, as plans often write)."""
    m = _BRACES.search(token)
    if not m:
        return [token]
    return [x for alt in m.group(1).split(",") for x in _expand(token[:m.start()] + alt.strip() + token[m.end():])]


def _clean(token: str, root: Path) -> str | None:
    token = token.strip().strip("\"'()[],;:").replace("\\", "/")
    if (not token or "://" in token or token.startswith(("mailto:", "#", "~", "$", "%"))
            or any(ch in token for ch in "<>*?|{}")):
        return None  # URLs, home-folder paths, placeholders and globs are not project files
    token = _SUFFIX.sub("", token)
    if token.startswith("./"):
        token = token[2:]
    if re.match(r"^[A-Za-z]:/", token) or token.startswith("/"):
        try:
            token = Path(token).resolve().relative_to(root.resolve()).as_posix()
        except (ValueError, OSError):
            return None
    return token or None


def mentions(text: str, root: Path) -> tuple[list[tuple[str, int]], list[tuple[str, int]]]:
    """(paths, dotted names) the plan mentions, with line numbers. Overlay blocks are left out."""
    lines = text.split("\n")
    for block in _overlay_blocks(lines):
        start, end = block.first - 1, min(block.last + 1, len(lines))  # with the fence lines
        lines[start:end] = [""] * (end - start)
    paths: list[tuple[str, int]] = []
    names: list[tuple[str, int]] = []
    seen: set[str] = set()
    for lineno, line in enumerate(lines, 1):
        candidates = [m.group(1) for m in _LINK.finditer(line)]
        candidates += [m.group(1) for m in _BARE.finditer(line)]
        for m in _TICK.finditer(line):
            words = m.group(1).split()
            if not words:
                continue
            token = words[0]
            if _DOTTED.match(token) and token not in seen:  # a module (a.b.c), or a root file (setup.py)
                seen.add(token)
                names.append((token, lineno))
            candidates.append(token)
        for raw in candidates:
            for expanded in _expand(raw):
                token = _clean(expanded, root)
                if token and _FILE_LIKE.search(token) and ("path", token) not in seen:
                    seen.add(("path", token))
                    paths.append((token, lineno))
    return paths, names


def _dirs(files: list[str]) -> set[str]:
    out = set()
    for f in files:
        parts = f.split("/")[:-1]
        for k in range(1, len(parts) + 1):
            out.add("/".join(parts[:k]))
    return out


class Resolver:
    """Maps a name from a plan to a repo path: exact, or a unique shortened suffix."""

    def __init__(self, files: list[str]) -> None:
        self.files = set(files)
        self.dirs = _dirs(files)
        self._by_name: dict[str, list[str]] = {}
        for f in files:
            self._by_name.setdefault(f.rsplit("/", 1)[-1], []).append(f)

    def path(self, token: str) -> tuple[str, bool] | None:
        """(repo path, exists) or None when it can't be placed (ambiguous, or nowhere to go)."""
        if token in self.files:
            return token, True
        name = token.rsplit("/", 1)[-1]
        matches = [f for f in self._by_name.get(name, []) if f.endswith("/" + token)]
        if len(matches) == 1:
            return matches[0], True
        if len(matches) > 1:
            return None
        parts = token.split("/")
        for k in range(len(parts) - 1, 0, -1):  # longest folder prefix that exists
            prefix = "/".join(parts[:k])
            rest = "/".join(parts[k:])
            if prefix in self.dirs:
                return token, False
            found = [d for d in self.dirs if d.endswith("/" + prefix)]
            if len(found) == 1:
                return f"{found[0]}/{rest}", False
            if len(found) > 1:
                return None
        return None


# -- applying a plan to a graph ---------------------------------------------------------------------


@dataclass
class Applied:
    graph: Graph  # the proposal: the baseline with new and moved nodes, edges, drops
    touched: set[str]  # node ids the plan edits
    issues: list[PlanIssue]


def apply_plan(base: Graph, text: str, files: list[str], provider, options: dict, root: Path) -> Applied:
    prop = base.copy()
    resolver = Resolver(files)
    touched: set[str] = set()
    other: dict[str, str] = {}
    issues: list[PlanIssue] = []
    index = prop.path_index()

    def place(rel: str, label: str | None = None) -> str | None:
        """Node id for a repo path, creating a node for a new file when the provider can."""
        nid = index.get(rel)
        if nid:
            return nid
        spec = provider.new_node(prop, rel, options)
        if spec is None:
            return None
        nid, default_label, group = spec
        if nid not in prop.nodes:
            prop.add_node(nid, label or default_label, group, [rel])
        elif rel not in prop.nodes[nid].paths:
            prop.nodes[nid].paths.append(rel)
        index[rel] = nid
        return nid

    moved: dict[str, str] = {}  # old node id -> new node id, so later lines may use either name

    def lookup(token: str) -> str | None:
        """An existing node for a name in an overlay line."""
        nid = token if token in prop.nodes or token in moved else provider.alias(prop, token, options)
        if nid is None:
            cleaned = _clean(token, root)
            found = resolver.path(cleaned) if cleaned else None
            nid = index.get(found[0]) if found else None
        while nid in moved:
            nid = moved[nid]
        return nid if nid in prop.nodes else None

    paths, names = mentions(text, root)
    for token, _ in paths:
        found = resolver.path(token)
        if found is None:
            if "/" in token:  # a bare name (notes.md, a.b.c) that matches nothing isn't worth listing
                other[token] = "not found in the project"
            continue
        rel, exists = found
        nid = index.get(rel) or place(rel)  # on disk but not at HEAD yet: still new to the baseline
        if nid is None:
            other[rel] = "not part of the graph" if exists else "new file outside the graph"
        elif nid in base.nodes:
            touched.add(nid)
    for name, _ in names:
        nid = name if name in prop.nodes else provider.alias(prop, name, options)
        if nid:
            touched.add(nid)

    ops, parse_issues = parse_overlay(text)
    issues += parse_issues
    # Order: new and moved nodes, then dependency changes, then removals (which take their
    # remaining edges with them). So a line may refer to a node another line adds or removes.
    for op in [o for o in ops if o.kind in ("add", "move")]:
        if op.kind == "add":
            existing = lookup(op.a)
            if existing:
                touched.add(existing)  # already there: the plan edits it
                continue
            cleaned = _clean(op.a, root)
            found = resolver.path(cleaned) if cleaned else None
            nid = place(found[0], op.label) if found else None
            if nid is None:
                issues.append(PlanIssue(op.line, f"can't place {op.a!r}: give a path inside an existing folder"))
            elif op.label:
                prop.nodes[nid].label = op.label
            continue
        old = lookup(op.a)
        if old is None:
            issues.append(PlanIssue(op.line, f"{op.a!r} is not in the diagram, so it can't be moved"))
            continue
        cleaned = _clean(op.b or "", root)
        found = resolver.path(cleaned) if cleaned else None
        new = place(found[0]) if found else None
        if new is None:
            issues.append(PlanIssue(op.line, f"can't place {op.b!r}: give a path inside an existing folder"))
            continue
        if new == old:
            continue
        prop.nodes[new].note = f"moved from {old}"
        for (src, dst), e in list(prop.edges.items()):
            if old in (src, dst):
                prop.add_edge(new if src == old else src, new if dst == old else dst, e.weak, e.label)
        prop.nodes.pop(old, None)
        prop.edges = {k: e for k, e in prop.edges.items() if old not in k}
        touched.discard(old)
        moved[old] = new
    for op in [o for o in ops if o.kind in ("link", "unlink")]:
        src, dst = lookup(op.a), lookup(op.b or "")
        missing = [t for t, n in ((op.a, src), (op.b, dst)) if n is None]
        if missing:
            issues.append(PlanIssue(op.line, f"{missing[0]!r} is not in the diagram; add it with `+ {missing[0]}` first"))
            continue
        if op.kind == "link":
            prop.add_edge(src, dst)
        elif (src, dst) in prop.edges:
            del prop.edges[(src, dst)]
        else:
            issues.append(PlanIssue(op.line, f"there is no dependency {op.a} -> {op.b} to remove"))
    for op in [o for o in ops if o.kind == "drop"]:
        nid = lookup(op.a)
        if nid is None:
            issues.append(PlanIssue(op.line, f"{op.a!r} is not in the diagram, so it can't be removed"))
            continue
        prop.nodes.pop(nid, None)
        prop.edges = {k: e for k, e in prop.edges.items() if nid not in k}
        touched.discard(nid)

    prop.other_files = [{"path": p, "reason": r} for p, r in sorted(other.items())]
    return Applied(prop, touched, sorted(issues, key=lambda i: i.line))


# -- which plan is active ---------------------------------------------------------------------------


@dataclass
class ActivePlan:
    path: Path
    source: str  # "cli", "serve", or the name of the editor or agent integration that set it
    time: float
    session: str | None = None

    @property
    def title(self) -> str:
        try:
            for line in read_text(self.path).split("\n"):
                if line.startswith("#"):
                    return line.lstrip("#").strip()
        except OSError:
            pass
        return self.path.stem


def state_dir(root: Path) -> Path:
    return mekhen_home() / "state" / project_key(root)


def state_file(root: Path) -> Path:
    return state_dir(root) / "active-plan.json"


def set_active_plan(root: Path, plan: Path, source: str, session: str | None = None) -> ActivePlan:
    active = ActivePlan(plan.resolve(), source, time.time(), session)
    path = state_file(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"schema": 1, "plan": str(active.path), "source": source, "time": active.time,
                               "session": session, "root": str(root)}), encoding="utf-8")
    os.replace(tmp, path)
    return active


def clear_active_plan(root: Path) -> None:
    state_file(root).unlink(missing_ok=True)


def active_plan(root: Path) -> ActivePlan | None:
    try:
        data = json.loads(state_file(root).read_text(encoding="utf-8"))
        plan = Path(data["plan"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not plan.is_file():
        return None
    return ActivePlan(plan, str(data.get("source") or "unknown"), float(data.get("time") or 0), data.get("session"))


def has_overlay(text: str) -> bool:
    return bool(_overlay_blocks(text.split("\n")))


def check_plan(text: str) -> list[PlanIssue]:
    """Overlay syntax problems only (no project needed): what a fast hook can report."""
    return parse_overlay(text)[1]
