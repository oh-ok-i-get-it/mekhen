"""The `files` provider: folders and files, for a project in any language. No edges.

Large trees stay readable because the viewer collapses folders where nothing is marked.
"""

from __future__ import annotations

import fnmatch
from typing import Any

from mekhen.core.graph import Graph
from mekhen.core.providers.base import Provider


def _excluded(rel: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(rel, pat) or pat in rel.split("/")[:-1] for pat in patterns)


def _dir_group(graph: Graph, folder: str) -> str | None:
    """Create the groups for `folder` and its parents; returns the folder's group id."""
    if not folder:
        return None
    parts = folder.split("/")
    parent = None
    for k in range(1, len(parts) + 1):
        path = "/".join(parts[:k])
        graph.add_group(path + "/", parts[k - 1] + "/", parent, path)
        parent = path + "/"
    return parent


def build(source, options: dict[str, Any]) -> Graph:
    graph = Graph(direction=options.get("direction") or "LR")
    patterns = list(options.get("exclude") or [])
    for rel in source.files():
        if _excluded(rel, patterns):
            continue
        folder, _, name = rel.rpartition("/")
        graph.add_node(rel, name, _dir_group(graph, folder), [rel])
    return graph


def inputs(files: list[str], options: dict[str, Any]) -> list[str]:
    return []  # only the list of files matters, not their content


def new_node(graph: Graph, rel: str, options: dict[str, Any]) -> tuple[str, str, str | None] | None:
    """A new file goes in its folder; new folders on the way are added to `graph` as groups."""
    folder, _, name = rel.rpartition("/")
    return rel, name, _dir_group(graph, folder)


PROVIDER = Provider("files", "Folders and files, for any language (no dependency edges)", build, inputs, new_node)
