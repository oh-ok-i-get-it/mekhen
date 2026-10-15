"""Graph providers: turn a project's files into a graph (see core/graph.py for the contract).

    python   import graph of Python packages (ast)
    files    folders and files, any language
    command  an external program printing graph JSON (user config only)
"""

from __future__ import annotations

from mekhen.core.providers import command, files, python
from mekhen.core.providers.base import Provider, ProviderError

__all__ = ["PROVIDERS", "Provider", "ProviderError", "detect"]

PROVIDERS: dict[str, Provider] = {p.name: p for p in (python.PROVIDER, files.PROVIDER, command.PROVIDER)}


def detect(all_files: list[str], options: dict | None = None) -> str:
    """`python` when the project has Python packages, otherwise `files`."""
    try:
        python.find_packages(all_files, (options or {}).get("packages"))
        return "python"
    except ProviderError:
        return "files"
