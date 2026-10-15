"""`mekhen gen imports`: the project's internal Python import graph as a Mermaid flowchart.

Files are read with `ast`; project code is never imported or run. Packages become nested
subgraphs, imports inside `if TYPE_CHECKING:` are drawn dotted, and the output is sorted so the
same code always gives byte-for-byte the same file.
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import re
import sys
from pathlib import Path
from typing import Any

from mekhen.core.conventions import generated_header
from mekhen.core.gen.base import GenError

SKIP_DIRS = {
    ".git", ".hg", ".venv", "venv", "env", ".env", "node_modules", "build", "dist", "__pycache__",
    ".tox", ".nox", ".mypy_cache", ".pytest_cache", ".ruff_cache", "site-packages", ".diagrams",
    "docs", "examples",
}
DEFAULT_EXCLUDE = ["tests", "test", "conftest"]


def add_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("--package", dest="packages", action="append", metavar="NAME",
                   help="package to include (repeatable); default: auto-detect")
    p.add_argument("--depth", type=int, metavar="N",
                   help="collapse module names to their first N parts (0 = full names)")
    p.add_argument("--exclude", action="append", metavar="PATTERN",
                   help="leave out modules: a name part (tests) or a glob on the dotted name (*.migrations.*)")
    p.add_argument("--include-external", action=argparse.BooleanOptionalAction, default=None,
                   help="also show third-party packages (standard library is never shown)")
    p.add_argument("--direction", choices=["LR", "RL", "TB", "TD", "BT"], help="layout direction (default LR)")


def _find_packages(root: Path, names: list[str] | None) -> list[tuple[str, Path]]:
    bases = [b for b in (root / "src", root) if b.is_dir()]
    if names:
        found = []
        for name in names:
            rel = Path(*name.split("."))
            for base in bases:
                if (base / rel).is_dir():
                    found.append((name, base / rel))
                    break
                if (base / rel).with_suffix(".py").is_file():
                    found.append((name, (base / rel).with_suffix(".py")))
                    break
            else:
                raise GenError(f"package {name!r} not found under {root} or {root / 'src'}")
        return found
    found = []
    seen = set()
    for base in bases:
        for child in sorted(base.iterdir()):
            if (child.is_dir() and child.name not in SKIP_DIRS and child.name.isidentifier()
                    and child.name not in seen and (child / "__init__.py").is_file()):
                found.append((child.name, child))
                seen.add(child.name)
    if not found:
        raise GenError("no Python packages found (a folder with __init__.py, in src/ or the project root); "
                       "name them with --package or [tool.mekhen.gen.imports] packages = [...]")
    return found


def _excluded(module: str, patterns: list[str]) -> bool:
    parts = module.split(".")
    for pat in patterns:
        if any(ch in pat for ch in ".*?["):
            if fnmatch.fnmatchcase(module, pat):
                return True
        elif pat in parts:
            return True
    return False


def _modules(root: Path, options: dict[str, Any]) -> dict[str, tuple[Path, bool]]:
    """{dotted module name: (file, is_package)}"""
    patterns = DEFAULT_EXCLUDE + list(options.get("exclude") or [])
    modules: dict[str, tuple[Path, bool]] = {}
    for name, path in _find_packages(root, options.get("packages")):
        if path.is_file():
            if not _excluded(name, patterns):
                modules[name] = (path, False)
            continue
        for py in sorted(path.rglob("*.py")):
            rel_parts = py.relative_to(path).with_suffix("").parts
            if any(part in SKIP_DIRS or not part.isidentifier() for part in rel_parts):
                continue
            is_pkg = rel_parts[-1] == "__init__"
            parts = rel_parts[:-1] if is_pkg else rel_parts
            module = ".".join((name, *parts))
            if not _excluded(module, patterns):
                modules[module] = (py, is_pkg)
    return modules


def fingerprint(root: Path, options: dict[str, Any]) -> tuple:
    try:
        modules = _modules(root, options)
    except GenError as e:
        return ("error", str(e))
    stats = []
    for name, (path, _) in sorted(modules.items()):
        try:
            st = path.stat()
            stats.append((name, st.st_mtime_ns, st.st_size))
        except OSError:
            stats.append((name, 0, 0))
    return (repr(sorted(options.items())), tuple(stats))


class _ImportVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.found: list[tuple[str, int, list[str] | None, bool]] = []
        self.type_checking = 0

    @staticmethod
    def _is_type_checking(test: ast.expr) -> bool:
        return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
            isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING")

    def visit_If(self, node: ast.If) -> None:
        if self._is_type_checking(node.test):
            self.type_checking += 1
            for child in node.body:
                self.visit(child)
            self.type_checking -= 1
            for child in node.orelse:
                self.visit(child)
        else:
            self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.found.append((alias.name, 0, None, self.type_checking > 0))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.found.append((node.module or "", node.level, [a.name for a in node.names], self.type_checking > 0))


def _longest_internal(name: str, internal: set[str]) -> str | None:
    parts = name.split(".")
    for k in range(len(parts), 0, -1):
        candidate = ".".join(parts[:k])
        if candidate in internal:
            return candidate
    return None


def _targets(module: str, is_pkg: bool, target: str, level: int, names: list[str] | None,
             internal: set[str]) -> tuple[set[str], set[str]]:
    """(internal modules, top-level external names) one import statement refers to."""
    if level:
        base_parts = module.split(".") if is_pkg else module.split(".")[:-1]
        up = level - 1
        if up > len(base_parts):
            return set(), set()
        base_parts = base_parts[:len(base_parts) - up]
        base = ".".join([*base_parts, *([target] if target else [])])
    else:
        base = target
    found: set[str] = set()
    if names is not None:
        for n in names:
            full = f"{base}.{n}" if base else n
            if n != "*" and full in internal:
                found.add(full)
                continue
            hit = _longest_internal(base, internal) if base else None
            if hit:
                found.add(hit)
    else:
        hit = _longest_internal(base, internal)
        if hit:
            found.add(hit)
    external = set()
    if not found and not level and base:
        external.add(base.split(".")[0])
    return found, external


def _id_safe(name: str) -> str:
    out = []
    for ch in name.replace(".", "__"):
        out.append(ch if re.match(r"[A-Za-z0-9_]", ch) else f"_x{ord(ch):04x}")
    return "".join(out)


def generate(root: Path, options: dict[str, Any], command: str) -> str:
    modules = _modules(root, options)
    internal = set(modules)
    depth = int(options.get("depth") or 0)
    include_external = bool(options.get("include_external"))
    direction = options.get("direction") or "LR"
    stdlib = set(getattr(sys, "stdlib_module_names", ())) | {"__future__"}
    tops = {m.split(".")[0] for m in internal}

    def collapse(name: str) -> str:
        return ".".join(name.split(".")[:depth]) if depth else name

    nodes: set[str] = {collapse(m) for m in internal}
    edges: dict[tuple[str, str], bool] = {}  # (src, dst) -> type-only
    externals: set[str] = set()
    problems: list[str] = []

    for module, (path, is_pkg) in sorted(modules.items()):
        try:
            tree = ast.parse(path.read_bytes(), filename=str(path))
        except (SyntaxError, ValueError) as e:
            problems.append(f"could not parse {path.relative_to(root).as_posix()}: {getattr(e, 'msg', e)}")
            continue
        visitor = _ImportVisitor()
        visitor.visit(tree)
        src = collapse(module)
        for target, level, names, type_only in visitor.found:
            found, external = _targets(module, is_pkg, target, level, names, internal)
            dests = {collapse(t) for t in found}
            if include_external:
                for ext in external:
                    if ext not in stdlib and ext not in tops:
                        externals.add(ext)
                        dests.add("\0" + ext)
            for dst in dests:
                if dst == src:
                    continue
                key = (src, dst)
                edges[key] = edges.get(key, True) and type_only

    def parent(name: str) -> str | None:
        return name.rsplit(".", 1)[0] if "." in name else None

    groups = {p for n in nodes for p in _ancestors(n)}

    def container(name: str) -> str | None:
        return name if name in groups else parent(name)

    def nid(name: str) -> str:
        return "x_" + _id_safe(name[1:]) if name.startswith("\0") else "m_" + _id_safe(name)

    lines = [generated_header("imports", command)]
    lines += [f"%% {p}" for p in problems]
    lines.append(f"flowchart {direction}")

    def emit(group: str | None, indent: int) -> None:
        pad = "    " * indent
        for name in sorted(n for n in nodes if container(n) == group):
            label = "__init__" if name in groups else name.rsplit(".", 1)[-1]
            lines.append(f'{pad}{nid(name)}["{label}"]')
        for sub in sorted(g for g in groups if parent(g) == group):
            lines.append(f'{pad}subgraph p_{_id_safe(sub)}["{sub}"]')
            emit(sub, indent + 1)
            lines.append(f"{pad}end")

    emit(None, 1)
    if externals:
        lines.append('    subgraph external["external"]')
        lines += [f'        {nid(chr(0) + e)}["{e}"]' for e in sorted(externals)]
        lines.append("    end")
    for (src, dst), type_only in sorted(edges.items()):
        lines.append(f"    {nid(src)} {'-.->' if type_only else '-->'} {nid(dst)}")
    return "\n".join(lines) + "\n"


def _ancestors(name: str) -> list[str]:
    parts = name.split(".")
    return [".".join(parts[:k]) for k in range(1, len(parts))]
