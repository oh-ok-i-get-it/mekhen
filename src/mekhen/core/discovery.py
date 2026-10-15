"""Find a project's diagrams: <store>/baseline/NAME.mmd and <store>/proposed/NAME.mmd, paired by name."""

from __future__ import annotations

import re
from pathlib import Path

from mekhen.core.conventions import GENERATED_PREFIX, HAND_WRITTEN
from mekhen.core.model import Diagram, Side
from mekhen.core.store import SIDES

EXTENSIONS = (".mmd", ".mermaid")
_DECL = re.compile(r"^\s*([A-Za-z][\w-]*)")


def read_text(path: Path) -> str:
    """UTF-8 (BOM stripped, undecodable bytes replaced) with newlines normalized to \\n."""
    data = path.read_bytes()
    return data.decode("utf-8-sig", errors="replace").replace("\r\n", "\n").replace("\r", "\n")


def strip_frontmatter(lines: list[str]) -> int:
    """Index of the first line after a leading `---` frontmatter block (0 when there is none)."""
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                return i + 1
    return 0


def detect_type(text: str) -> str | None:
    lines = text.split("\n")
    for line in lines[strip_frontmatter(lines):]:
        stripped = line.strip()
        if not stripped or stripped.startswith("%%"):
            continue
        m = _DECL.match(stripped)
        if not m:
            return None
        word = m.group(1)
        return "flowchart" if word in ("flowchart", "graph", "flowchart-elk") else word
    return None


def detect_origin(text: str) -> tuple[str, str | None]:
    """("generated", "<gen args>") | ("hand-written", None) | ("unknown", None), from leading comments."""
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith("%%"):
            break
        if stripped.startswith(GENERATED_PREFIX):
            marker = "run: mekhen gen "
            idx = stripped.find(marker)
            return "generated", (stripped[idx + len(marker):].strip() if idx >= 0 else "")
        if stripped.startswith(HAND_WRITTEN):
            return "hand-written", None
    return "unknown", None


def load_side(role: str, path: Path) -> Side:
    stat = path.stat()
    text = read_text(path)
    origin, gen_command = detect_origin(text)
    return Side(role, path, text, stat.st_mtime_ns, stat.st_size, origin, gen_command, detect_type(text))


def side_files(store: Path) -> dict[tuple[str, str], Path]:
    """{(name, role): path} for every diagram file in the store."""
    found: dict[tuple[str, str], Path] = {}
    for role in SIDES:
        folder = store / role
        if not folder.is_dir():
            continue
        for path in sorted(folder.iterdir()):
            if path.is_file() and path.suffix.lower() in EXTENSIONS:
                found.setdefault((path.stem, role), path)
    return found


def discover(store: Path) -> dict[str, Diagram]:
    diagrams: dict[str, Diagram] = {}
    for (name, role), path in sorted(side_files(store).items()):
        try:
            side = load_side(role, path)
        except OSError:
            continue  # deleted or locked between listing and reading; the next scan catches up
        diagram = diagrams.setdefault(name, Diagram(id=name, kind="pair"))
        setattr(diagram, role, side)
    return diagrams
