"""Data shared by the CLI's --json output and the server's API (one schema for both)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

SCHEMA = 1


@dataclass
class Side:
    """One file: a diagram's baseline or its proposal."""

    role: str  # "baseline" | "proposed"
    path: Path
    text: str
    mtime_ns: int
    size: int
    origin: str  # "generated" | "hand-written" | "unknown"
    gen_command: str | None  # for generated files: the `mekhen gen ...` arguments that made it
    diagram_type: str | None  # "flowchart", "sequenceDiagram", ... or None when undeclared

    @property
    def signature(self) -> tuple[int, int]:
        return (self.mtime_ns, self.size)


@dataclass
class Diagram:
    id: str
    kind: str  # "pair" (Markdown blocks arrive in v0.2)
    baseline: Side | None = None
    proposed: Side | None = None

    @property
    def sides(self) -> list[Side]:
        return [s for s in (self.baseline, self.proposed) if s is not None]

    @property
    def diagram_type(self) -> str | None:
        types = {s.diagram_type for s in self.sides if s.diagram_type}
        return types.pop() if len(types) == 1 else ("mixed" if types else None)


@dataclass
class LintIssue:
    severity: str  # "error" | "warning"
    code: str
    message: str
    path: Path | None = None
    line: int | None = None
    role: str | None = None
    diagram: str | None = None

    def to_json(self, root: Path) -> dict:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "path": display_path(self.path, root) if self.path else None,
            "line": self.line,
            "side": self.role,
            "diagram": self.diagram,
        }


@dataclass
class ParseError:
    line: int
    message: str


@dataclass
class Summary:
    """Counts shown in the sidebar and by `mekhen list`."""

    added: int = 0
    removed: int = 0
    changed: int = 0
    extra: dict = field(default_factory=dict)


def display_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)
