"""What a provider is: something that turns a project's files into a graph."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


class ProviderError(Exception):
    """A provider cannot build a graph for this project with these options."""


@dataclass(frozen=True)
class Provider:
    name: str
    help: str
    # (source, options) -> Graph
    build: Callable[..., Any]
    # (all files, options) -> the files whose *content* the graph depends on (for change detection)
    inputs: Callable[[list[str], dict], list[str]]
    # (graph, path, options) -> (node id, label, group) for a file that isn't in the graph yet, or None.
    # May add the groups the new file needs (new folders, new subpackages) to the graph.
    new_node: Callable[..., tuple[str, str, str | None] | None]
    # (graph, name, options) -> node id for a name such as a dotted module, or None
    alias: Callable[..., str | None] = lambda graph, name, options: None


def deepest_group(graph, rel: str):
    """The group whose folder (`path`) most closely contains `rel`."""
    best = None
    for g in graph.groups.values():
        path = getattr(g, "path", None)
        if path is not None and (path == "" or rel.startswith(path + "/")):
            if best is None or len(path) > len(best.path):
                best = g
    return best
