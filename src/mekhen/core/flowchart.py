"""A parser for the Mermaid flowchart syntax that mekhen lints and diffs.

It is not a full Mermaid validator (the viewer shows Mermaid's own errors). It knows enough to
find every node, edge and subgraph, where each is declared, and each edge's index in Mermaid's
declaration order (what `linkStyle N` refers to):

- comments (`%%` at the start of a line), `%%{init}%%` directives and `---` frontmatter
- statements split on newlines and `;` (outside quotes; a quoted label may span lines)
- nodes with every bracket shape, `@{ shape: ..., label: ... }`, and `:::class`
- edges `-->` `---` `-.->` `==>` `~~~`, with `o`/`x`/`<` ends, `|text|` and `-- text -->`
  labels, `&` groups, chains (`a --> b --> c`) and edge ids (`e1@-->`)
- `subgraph id ["title"] ... end`, `direction`, `classDef`, `class`, `style`, `linkStyle`, `click`
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from mekhen.core.discovery import strip_frontmatter
from mekhen.core.model import ParseError

_HEADER = re.compile(r"^(flowchart|graph|flowchart-elk)(?:\s+(TB|TD|BT|RL|LR))?\s*$")
_IGNORED = re.compile(r"^(direction\s|classDef\s|linkStyle\s|click\s|accTitle\s*:|accDescr\s*[:{])")
_CLASS_STMT = re.compile(r"^class\s+(\S+)\s+(\S+)\s*$")
_STYLE_STMT = re.compile(r"^style\s+(\S+)\s")
_SUBGRAPH_BRACKET = re.compile(r'^([^\s\[\]"]+)\s*\[\s*(".*"|[^\]]*?)\s*\]$', re.S)
_CLASS_SUFFIX = re.compile(r":::([\w-]+)")
_SHAPE_KV = re.compile(r'(\w+)\s*:\s*("([^"]*)"|[^,}]*)')

# Opening bracket -> {closing bracket: shape name}; longest openers first.
_SHAPES: list[tuple[str, dict[str, str]]] = [
    ("(((", {")))": "double-circle"}),
    ("([", {"])": "stadium"}),
    ("[[", {"]]": "subroutine"}),
    ("[(", {")]": "cylinder"}),
    ("((", {"))": "circle"}),
    ("{{", {"}}": "hexagon"}),
    ("[/", {"/]": "lean-right", "\\]": "trapezoid"}),
    ("[\\", {"\\]": "lean-left", "/]": "trapezoid-alt"}),
    ("[", {"]": "rect"}),
    ("(", {")": "round"}),
    ("{", {"}": "diamond"}),
    (">", {"]": "asymmetric"}),
]

_HEAD = r"(?P<head>>|[ox](?![\w]))?"
_EID = r"(?:(?P<eid>[A-Za-z_]\w*)@)?"
_PLAIN_LINK = re.compile(
    _EID + r"(?P<tail><|[ox](?=[-=.]))?(?P<body>-{2,}(?=[>ox])|-{3,}|-\.+-|={2,}(?=[>ox])|={3,}|~{3,})" + _HEAD
)
_TEXT_LINKS = [
    re.compile(_EID + r'(?P<tail><|[ox](?=-))?--(?![-.>])\s*(?P<text>"[^"]*"|.*?)\s*(?P<body>-{2,})' + _HEAD, re.S),
    re.compile(_EID + r'(?P<tail><|[ox](?=-))?-\.(?![-.>])\s*(?P<text>"[^"]*"|.*?)\s*(?P<body>\.-+)' + _HEAD, re.S),
    re.compile(_EID + r'(?P<tail><|[ox](?==))?==(?![=>])\s*(?P<text>"[^"]*"|.*?)\s*(?P<body>={2,})' + _HEAD, re.S),
]


@dataclass
class Label:
    text: str
    quoted: bool
    line: int
    owner: str  # e.g. "node a", "edge a --> b", "subgraph s"


@dataclass
class Node:
    id: str
    line: int
    label: str | None = None
    quoted: bool | None = None
    shape: str = "default"
    parent: str | None = None
    classes: list[str] = field(default_factory=list)


@dataclass
class Edge:
    src: str
    dst: str
    kind: str  # "solid" | "dotted" | "thick" | "invisible"
    head: str  # ">" "o" "x" or ""
    tail: str  # "<" "o" "x" or ""
    label: str | None
    index: int  # position in declaration order: what `linkStyle <index>` targets
    line: int
    eid: str | None = None

    @property
    def arrow(self) -> str:
        """Canonical Mermaid spelling, e.g. `-->`, `-.->`, `==>`, `<-->`, `--o`, `~~~`."""
        if self.kind == "invisible":
            return "~~~"
        if self.kind == "dotted":
            return f"{self.tail}-.-{self.head}"
        if self.kind == "thick":
            return f"{self.tail}=={self.head or '='}"
        return f"{self.tail}--{self.head or '-'}"


@dataclass
class Subgraph:
    id: str
    title: str | None
    parent: str | None
    line: int


@dataclass
class Flowchart:
    direction: str | None = None
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    subgraphs: dict[str, Subgraph] = field(default_factory=dict)
    labels: list[Label] = field(default_factory=list)
    ids_used: list[tuple[str, int]] = field(default_factory=list)
    styled: set[str] = field(default_factory=set)
    errors: list[ParseError] = field(default_factory=list)


class _Error(Exception):
    pass


def _statements(text: str) -> list[tuple[int, str]]:
    """(line number, statement) pairs, comments removed."""
    lines = text.split("\n")
    first = strip_frontmatter(lines)
    body = "\n".join(lines[first:])
    out: list[tuple[int, str]] = []
    buf: list[str] = []
    buf_line = line = first + 1
    in_quote = False
    only_ws = True  # nothing but whitespace since the last newline
    i, n = 0, len(body)

    def flush() -> None:
        stmt = "".join(buf).strip()
        if stmt:
            out.append((buf_line, stmt))
        buf.clear()

    while i < n:
        c = body[i]
        if not in_quote and only_ws and body.startswith("%%", i):
            while i < n and body[i] != "\n":
                i += 1
            continue
        if c == "\n":
            line += 1
            only_ws = True
            if in_quote:
                buf.append(c)
            else:
                flush()
                buf_line = line
            i += 1
            continue
        if c == ";" and not in_quote:
            flush()
            buf_line = line
            i += 1
            continue
        if c == '"':
            in_quote = not in_quote
        if not c.isspace():
            if not "".join(buf).strip():
                buf_line = line
            only_ws = False
        buf.append(c)
        i += 1
    flush()
    return out


class _Parser:
    def __init__(self) -> None:
        self.fc = Flowchart()
        self.stack: list[str] = []
        self.edge_count = 0

    # -- node and subgraph bookkeeping ------------------------------------------------------

    def touch(self, node_id: str, line: int, *, label: str | None = None, quoted: bool | None = None,
              shape: str | None = None) -> None:
        self.fc.ids_used.append((node_id, line))
        node = self.fc.nodes.get(node_id)
        if node is None:
            node = self.fc.nodes[node_id] = Node(node_id, line)
        if shape is not None:
            node.label, node.quoted, node.shape = label, quoted, shape
            if label is not None:
                self.fc.labels.append(Label(label, bool(quoted), line, f"node {node_id}"))
        if self.stack and node.parent is None:
            node.parent = self.stack[-1]

    def open_subgraph(self, rest: str, line: int) -> None:
        rest = rest.strip()
        if not rest:
            raise _Error("subgraph needs an id or a title")
        quoted = False
        m = _SUBGRAPH_BRACKET.match(rest)
        if m:
            sid, title = m.group(1), m.group(2)
            if title.startswith('"') and title.endswith('"') and len(title) >= 2:
                title, quoted = title[1:-1], True
        elif rest.startswith('"') and rest.endswith('"') and len(rest) >= 2:
            sid = title = rest[1:-1]
            quoted = True
        else:
            sid = title = rest
        self.fc.ids_used.append((sid, line))
        self.fc.labels.append(Label(title, quoted, line, f"subgraph {sid}"))
        parent = self.stack[-1] if self.stack else None
        self.fc.subgraphs[sid] = Subgraph(sid, title, parent, line)
        self.stack.append(sid)

    # -- scanning ----------------------------------------------------------------------------

    def parse(self, text: str) -> Flowchart:
        statements = _statements(text)
        if not statements:
            self.fc.errors.append(ParseError(1, "empty diagram: expected `flowchart TD` (or LR, BT, RL)"))
            return self.fc
        line, header = statements[0]
        m = _HEADER.match(header)
        if not m:
            self.fc.errors.append(ParseError(line, f"expected a flowchart declaration such as `flowchart TD`, got {header[:40]!r}"))
            return self.fc
        self.fc.direction = m.group(2)
        for line, stmt in statements[1:]:
            try:
                self.statement(stmt, line)
            except _Error as e:
                self.fc.errors.append(ParseError(line, str(e)))
        for sid in self.stack:
            self.fc.errors.append(ParseError(self.fc.subgraphs[sid].line, f"subgraph {sid!r} is never closed with `end`"))
        for sid in self.fc.subgraphs:
            node = self.fc.nodes.get(sid)
            if node is not None and node.label is None:
                del self.fc.nodes[sid]  # an edge to a subgraph, not a separate node
        return self.fc

    def statement(self, stmt: str, line: int) -> None:
        word = stmt.split(None, 1)[0]
        if word == "subgraph":
            self.open_subgraph(stmt[len("subgraph"):], line)
            return
        if stmt == "end":
            if not self.stack:
                raise _Error("`end` without an open subgraph (a node id named end also causes this)")
            self.stack.pop()
            return
        if _IGNORED.match(stmt):
            return
        if word == "class":
            m = _CLASS_STMT.match(stmt)
            if not m:
                raise _Error("expected `class id1,id2 className`")
            for node_id in m.group(1).split(","):
                if node_id in self.fc.nodes:
                    self.fc.nodes[node_id].classes.append(m.group(2))
            return
        if word == "style":
            m = _STYLE_STMT.match(stmt)
            if not m:
                raise _Error("expected `style id css`")
            self.fc.styled.add(m.group(1))
            return
        self.chain(stmt, line)

    def chain(self, s: str, line: int) -> None:
        pos, group = self.group(s, 0, line)
        while True:
            pos = _skip_ws(s, pos)
            if pos >= len(s):
                return
            link, pos = self.link(s, pos, line)
            if link is None:
                raise _Error(f"unexpected text {s[pos:pos + 30]!r}; expected an arrow such as -->")
            pos = _skip_ws(s, pos)
            if pos >= len(s):
                raise _Error("arrow with no target node")
            pos, targets = self.group(s, pos, line)
            kind, head, tail, label, eid = link
            for src in group:
                for dst in targets:
                    self.fc.edges.append(Edge(src, dst, kind, head, tail, label, self.edge_count, line, eid))
                    self.edge_count += 1
                    if label is not None:
                        self.fc.labels.append(Label(label, False, line, f"edge {src} --> {dst}"))
            group = targets

    def group(self, s: str, pos: int, line: int) -> tuple[int, list[str]]:
        ids = []
        while True:
            pos, node_id = self.node(s, pos, line)
            ids.append(node_id)
            after = _skip_ws(s, pos)
            if after < len(s) and s[after] == "&":
                pos = _skip_ws(s, after + 1)
                continue
            return pos, ids

    def node(self, s: str, pos: int, line: int) -> tuple[int, str]:
        start = pos
        n = len(s)
        while pos < n:
            c = s[pos]
            if c.isalnum() or c in "_$":
                pos += 1
            elif c in "-." and pos > start and pos + 1 < n and (s[pos + 1].isalnum() or s[pos + 1] in "_$"):
                pos += 1
            else:
                break
        if pos == start:
            raise _Error(f"expected a node id at {s[start:start + 30]!r}")
        node_id = s[start:pos]

        if s.startswith("@{", pos):
            close = _find_unquoted(s, "}", pos + 2)
            if close < 0:
                raise _Error(f"unclosed @{{ ... }} after node {node_id!r}")
            props = {m.group(1): (m.group(3) if m.group(3) is not None else m.group(2).strip(), m.group(3) is not None)
                     for m in _SHAPE_KV.finditer(s[pos + 2:close])}
            label, quoted = props.get("label", (None, None))
            shape = props.get("shape", ("rect", False))[0] or "rect"
            self.touch(node_id, line, label=label, quoted=quoted if label is not None else None, shape=shape)
            return self.class_suffix(s, close + 1, node_id), node_id

        probe = pos
        while probe < n and s[probe] in " \t":
            probe += 1
        for opener, closers in _SHAPES:
            if not s.startswith(opener, probe) or (opener == ">" and probe != pos):
                continue
            label_pos = probe + len(opener)
            label, quoted, end, shape = self.label(s, label_pos, closers, node_id)
            self.touch(node_id, line, label=label, quoted=quoted, shape=shape)
            return self.class_suffix(s, end, node_id), node_id
        self.touch(node_id, line)
        return self.class_suffix(s, pos, node_id), node_id

    def label(self, s: str, pos: int, closers: dict[str, str], node_id: str) -> tuple[str, bool, int, str]:
        while pos < len(s) and s[pos] in " \t":
            pos += 1
        if pos < len(s) and s[pos] == '"':
            close_quote = s.find('"', pos + 1)
            if close_quote < 0:
                raise _Error(f"unclosed quote in the label of {node_id!r}")
            text = s[pos + 1:close_quote]
            after = close_quote + 1
            while after < len(s) and s[after] in " \t":
                after += 1
            for closer, shape in closers.items():
                if s.startswith(closer, after):
                    return text, True, after + len(closer), shape
            raise _Error(f"unexpected text after the label of {node_id!r}: {s[after:after + 20]!r} "
                         "(is there a double quote inside the label?)")
        best = None
        for closer, shape in closers.items():
            idx = s.find(closer, pos)
            if idx >= 0 and (best is None or idx < best[0]):
                best = (idx, closer, shape)
        if best is None:
            raise _Error(f"unclosed bracket in the label of {node_id!r}")
        idx, closer, shape = best
        return s[pos:idx].strip(), False, idx + len(closer), shape

    def class_suffix(self, s: str, pos: int, node_id: str) -> int:
        m = _CLASS_SUFFIX.match(s, pos)
        if m:
            self.fc.nodes[node_id].classes.append(m.group(1))
            return m.end()
        return pos

    def link(self, s: str, pos: int, line: int):
        m = _PLAIN_LINK.match(s, pos)
        if m and not (m.group("body") in ("--", "==") and not m.group("head")):
            body = m.group("body")
            end = m.end()
            label = None
            after = _skip_ws(s, end)
            if after < len(s) and s[after] == "|":
                close = _find_unquoted(s, "|", after + 1)
                if close < 0:
                    raise _Error("unclosed |edge label|")
                label = _unquote(s[after + 1:close].strip())
                end = close + 1
            return (_kind(body), m.group("head") or "", m.group("tail") or "", label, m.group("eid")), end
        for pattern in _TEXT_LINKS:
            m = pattern.match(s, pos)
            if not m:
                continue
            body = m.group("body")
            if body.startswith("-") and not m.group("head") and len(body) < 3:
                continue
            if body.startswith("=") and not m.group("head") and len(body) < 3:
                continue
            kind = "dotted" if body.startswith(".") else _kind(body)
            return (kind, m.group("head") or "", m.group("tail") or "", _unquote(m.group("text").strip()), m.group("eid")), m.end()
        return None, pos


def _kind(body: str) -> str:
    if body.startswith("~"):
        return "invisible"
    if body.startswith("="):
        return "thick"
    return "dotted" if "." in body else "solid"


def _unquote(text: str) -> str:
    if len(text) >= 2 and text.startswith('"') and text.endswith('"'):
        return text[1:-1]
    return text


def _skip_ws(s: str, pos: int) -> int:
    while pos < len(s) and s[pos].isspace():
        pos += 1
    return pos


def _find_unquoted(s: str, ch: str, pos: int) -> int:
    in_quote = False
    for i in range(pos, len(s)):
        if s[i] == '"':
            in_quote = not in_quote
        elif s[i] == ch and not in_quote:
            return i
    return -1


def parse(text: str) -> Flowchart:
    return _Parser().parse(text)
