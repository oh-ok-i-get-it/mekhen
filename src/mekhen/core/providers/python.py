"""The `python` provider: a project's internal import graph.

Files are read with `ast`; project code is never imported or run. Packages become nested groups,
imports inside `if TYPE_CHECKING:` are weak (dotted) edges, and the graph is the same for the same
code. It reads through a source, so it works on the working tree and on a git revision alike.
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import sys
from typing import Any

from mekhen.core.graph import Graph
from mekhen.core.providers.base import Provider, ProviderError, deepest_group

SKIP_DIRS = {
    ".git", ".hg", ".venv", "venv", "env", ".env", "node_modules", "build", "dist", "__pycache__",
    ".tox", ".nox", ".mypy_cache", ".pytest_cache", ".ruff_cache", "site-packages", ".diagrams",
    "docs", "examples",
}
DEFAULT_EXCLUDE = ["tests", "test", "conftest"]
EXTERNAL_GROUP = "(external)"


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


def _bases(files: list[str]) -> list[str]:
    """Where top-level packages may sit: src/ (when present) and the project root."""
    return (["src/"] if any(f.startswith("src/") for f in files) else []) + [""]


def find_packages(files: list[str], names: list[str] | None) -> list[tuple[str, str, bool]]:
    """[(package name, path, is a folder)] for the named packages, or every top-level one found."""
    fileset = set(files)
    bases = _bases(files)
    found: list[tuple[str, str, bool]] = []
    if names:
        for name in names:
            rel = name.replace(".", "/")
            for base in bases:
                if f"{base}{rel}/__init__.py" in fileset or any(f.startswith(f"{base}{rel}/") and f.endswith(".py")
                                                                for f in files):
                    found.append((name, base + rel, True))
                    break
                if f"{base}{rel}.py" in fileset:
                    found.append((name, f"{base}{rel}.py", False))
                    break
            else:
                raise ProviderError(f"package {name!r} not found in the project root or src/")
        return found
    seen: set[str] = set()
    for base in bases:
        for f in files:
            if not f.startswith(base):
                continue
            parts = f[len(base):].split("/")
            if (len(parts) == 2 and parts[1] == "__init__.py" and parts[0].isidentifier()
                    and parts[0] not in SKIP_DIRS and parts[0] not in seen):
                found.append((parts[0], base + parts[0], True))
                seen.add(parts[0])
    if not found:
        raise ProviderError("no Python packages found (a folder with __init__.py, in src/ or the project root); "
                            "name them with --package or packages = [...] in the python provider options")
    return sorted(found)


def _excluded(module: str, patterns: list[str]) -> bool:
    parts = module.split(".")
    for pat in patterns:
        if any(ch in pat for ch in ".*?["):
            if fnmatch.fnmatchcase(module, pat):
                return True
        elif pat in parts:
            return True
    return False


def modules(files: list[str], options: dict[str, Any]) -> dict[str, tuple[str, bool]]:
    """{dotted module name: (path, is_package)}"""
    patterns = DEFAULT_EXCLUDE + list(options.get("exclude") or [])
    out: dict[str, tuple[str, bool]] = {}
    for name, path, is_dir in find_packages(files, options.get("packages")):
        if not is_dir:
            if not _excluded(name, patterns):
                out[name] = (path, False)
            continue
        prefix = path + "/"
        for f in files:
            if not (f.startswith(prefix) and f.endswith(".py")):
                continue
            rel_parts = f[len(prefix):-3].split("/")
            if any(part in SKIP_DIRS or not part.isidentifier() for part in rel_parts):
                continue
            is_pkg = rel_parts[-1] == "__init__"
            parts = rel_parts[:-1] if is_pkg else rel_parts
            module = ".".join((name, *parts))
            if not _excluded(module, patterns):
                out[module] = (f, is_pkg)
    return out


def inputs(files: list[str], options: dict[str, Any]) -> list[str]:
    try:
        return sorted(path for path, _ in modules(files, options).values())
    except ProviderError:
        return []


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


def _ancestors(name: str) -> list[str]:
    parts = name.split(".")
    return [".".join(parts[:k]) for k in range(1, len(parts))]


def _parent(name: str) -> str | None:
    return name.rsplit(".", 1)[0] if "." in name else None


def _collapse(name: str, depth: int) -> str:
    return ".".join(name.split(".")[:depth]) if depth else name


def build(source, options: dict[str, Any]) -> Graph:
    files = source.files()
    mods = modules(files, options)
    internal = set(mods)
    depth = int(options.get("depth") or 0)
    include_external = bool(options.get("include_external"))
    stdlib = set(getattr(sys, "stdlib_module_names", ())) | {"__future__"}
    tops = {m.split(".")[0] for m in internal}
    graph = Graph(direction=options.get("direction") or "LR")

    names = {_collapse(m, depth) for m in internal}
    groups = {p for n in names for p in _ancestors(n)}
    roots = {name: path for name, path, is_dir in find_packages(files, options.get("packages")) if is_dir}

    def folder(gid: str) -> str | None:
        for name in sorted(roots, key=len, reverse=True):  # --package may name a subpackage: a.b
            if gid == name or gid.startswith(name + "."):
                rest = gid[len(name) + 1:]
                return roots[name] + ("/" + rest.replace(".", "/") if rest else "")
        return None

    for gid in sorted(groups):
        graph.add_group(gid, gid, _parent(gid), folder(gid))
    for module, (path, _) in sorted(mods.items()):
        name = _collapse(module, depth)
        label = "__init__" if name in groups else name.rsplit(".", 1)[-1]
        graph.add_node(name, label, name if name in groups else _parent(name), [path])

    contents = source.read([path for path, _ in mods.values()])
    for module, (path, is_pkg) in sorted(mods.items()):
        data = contents.get(path)
        if data is None:
            continue
        try:
            tree = ast.parse(data, filename=path)
        except (SyntaxError, ValueError) as e:
            graph.hints.append(f"could not parse {path}: {getattr(e, 'msg', e)}")
            continue
        visitor = _ImportVisitor()
        visitor.visit(tree)
        src = _collapse(module, depth)
        for target, level, imported, type_only in visitor.found:
            found, external = _targets(module, is_pkg, target, level, imported, internal)
            dests = {_collapse(t, depth) for t in found}
            if include_external:
                for ext in external:
                    if ext not in stdlib and ext not in tops:
                        graph.add_group(EXTERNAL_GROUP, "external")
                        graph.add_node(f"external:{ext}", ext, EXTERNAL_GROUP)
                        dests.add(f"external:{ext}")
            for dst in dests:
                if dst != src:
                    graph.add_edge(src, dst, weak=type_only)
    return graph


def new_node(graph: Graph, rel: str, options: dict[str, Any]) -> tuple[str, str, str | None] | None:
    """Where a not-yet-existing .py file would sit: its module name inside the nearest package.
    New subpackages on the way are added to `graph` as groups."""
    if not rel.endswith(".py"):
        return None
    group = deepest_group(graph, rel)
    if group is None:
        return None
    rest = rel[len(group.path) + 1:-3].split("/")
    if rest[-1] == "__init__":
        rest = rest[:-1]
    if not rest or not all(part.isidentifier() for part in rest):
        return None
    depth = int(options.get("depth") or 0)
    name = _collapse(".".join([group.id, *rest]), depth)
    existing = graph.nodes.get(name)
    if existing is not None:
        return existing.id, existing.label, existing.group
    parent = group.id
    for k in range(1, len(rest)):  # new subpackages between the existing package and the module
        gid = ".".join([group.id, *rest[:k]])
        if depth and len(gid.split(".")) >= depth:
            break
        graph.add_group(gid, gid, parent, f"{group.path}/{'/'.join(rest[:k])}")
        parent = gid
    return name, name.rsplit(".", 1)[-1], parent


def alias(graph: Graph, name: str, options: dict[str, Any]) -> str | None:
    """A dotted module name (possibly deeper than the graph's --depth) to its node."""
    if not name.replace(".", "").replace("_", "").isalnum():
        return None
    collapsed = _collapse(name, int(options.get("depth") or 0))
    return collapsed if collapsed in graph.nodes else None


PROVIDER = Provider(
    "python",
    "Python import graph, grouped by package (reads files with ast, never imports them)",
    build,
    inputs,
    new_node,
    alias,
)
