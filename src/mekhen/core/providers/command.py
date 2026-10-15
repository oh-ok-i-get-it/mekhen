"""The `command` provider: any program that prints a graph as JSON (schema in docs/graph-schema.md).

This is how other languages plug in, for example a small script around madge, dependency-cruiser
or `go list -json`. The command runs in the project folder (or, for HEAD, in a temporary export of
it) with MEKHEN_SOURCE set to "worktree" or "git". It is only ever taken from the user's own config,
never from a file inside a project, so opening a repo can't make mekhen run its commands.
"""

from __future__ import annotations

import fnmatch
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from mekhen.core.graph import Graph, SchemaError, from_json
from mekhen.core.providers.base import Provider, ProviderError, deepest_group


def _run(command: str, cwd: Path, env_extra: dict[str, str], timeout: float) -> Graph:
    env = {**os.environ, **env_extra}
    try:
        result = subprocess.run(command, shell=True, cwd=cwd, env=env, capture_output=True, timeout=timeout)  # noqa: S602 - user's own config
    except subprocess.TimeoutExpired as e:
        raise ProviderError(f"provider command timed out after {timeout:g}s: {command}") from e
    except OSError as e:
        raise ProviderError(f"provider command failed to start: {e}") from e
    if result.returncode != 0:
        tail = result.stderr.decode("utf-8", "replace").strip()[-500:]
        raise ProviderError(f"provider command exited with {result.returncode}: {command}\n{tail}")
    try:
        return from_json(json.loads(result.stdout.decode("utf-8")))
    except (ValueError, SchemaError) as e:
        raise ProviderError(f"provider command printed invalid graph JSON: {e}") from e


def build(source, options: dict[str, Any]) -> Graph:
    command = options.get("command")
    if not command:
        raise ProviderError("the command provider needs `command = \"...\"` in ~/.mekhen/config.toml")
    timeout = float(options.get("timeout") or 120)
    if source.kind == "git":
        with tempfile.TemporaryDirectory(prefix="mekhen-") as tmp:
            source.export(Path(tmp))
            return _run(command, Path(tmp), {"MEKHEN_SOURCE": "git", "MEKHEN_COMMIT": source.commit}, timeout)
    return _run(command, source.root, {"MEKHEN_SOURCE": "worktree"}, timeout)


def inputs(files: list[str], options: dict[str, Any]) -> list[str]:
    patterns = options.get("inputs") or ["*"]
    return [f for f in files if any(fnmatch.fnmatchcase(f, p) for p in patterns)]


def new_node(graph: Graph, rel: str, options: dict[str, Any]) -> tuple[str, str, str | None] | None:
    group = deepest_group(graph, rel)
    if group is None:
        return None
    return rel, rel.rsplit("/", 1)[-1], group.id


PROVIDER = Provider("command", "Any program that prints graph JSON (configured in ~/.mekhen/config.toml)",
                    build, inputs, new_node)
