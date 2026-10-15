"""`mekhen gen imports`: the python provider's graph, written out as a Mermaid file.

The graph itself comes from core/providers/python.py; this only adds the generated-file header
(which records the arguments, for staleness checks) and renders it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mekhen.core.conventions import generated_header
from mekhen.core.graph import to_mermaid
from mekhen.core.providers import python
from mekhen.core.sources import WorkingTree

add_arguments = python.add_arguments


def fingerprint(root: Path, options: dict[str, Any]) -> tuple:
    source = WorkingTree(root)
    try:
        files = source.files()
    except Exception as e:  # noqa: BLE001 - reported as a fingerprint, never raised
        return ("error", str(e))
    paths = python.inputs(files, options)
    if not paths:
        return ("error", "no Python packages found")
    return (repr(sorted(options.items())), source.key(paths))


def generate(root: Path, options: dict[str, Any], command: str) -> str:
    graph = python.build(WorkingTree(root), options)
    header = "\n".join([generated_header("imports", command), *(f"%% {h}" for h in graph.hints)])
    return to_mermaid(graph, highlight=False, header=header)
