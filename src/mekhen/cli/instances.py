"""Registry of running `mekhen serve` processes, kept in the user cache dir (never in a project).

One file per project root, so a second `mekhen serve` reuses the running server.
"""

from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from mekhen import __version__
from mekhen.core.config import cache_dir
from mekhen.core.store import project_key

# Never send requests for 127.0.0.1 through a configured HTTP proxy.
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


@dataclass
class Instance:
    port: int
    url: str
    viewers: int


def _file(root: Path) -> Path:
    return cache_dir() / "servers" / f"{project_key(root)}.json"


def health(port: int, timeout: float = 1.0) -> dict | None:
    try:
        with _opener.open(f"http://127.0.0.1:{port}/api/health", timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except (OSError, ValueError):
        return None


def find(root: Path) -> Instance | None:
    path = _file(root)
    try:
        port = int(json.loads(path.read_text(encoding="utf-8"))["port"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    data = health(port)
    if (data and data.get("app") == "mekhen"
            and os.path.normcase(str(data.get("root"))) == os.path.normcase(str(root))):
        return Instance(port, f"http://127.0.0.1:{port}/", int(data.get("viewers", 0)))
    path.unlink(missing_ok=True)  # stale entry: that server is gone
    return None


def register(root: Path, port: int) -> None:
    path = _file(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"port": port, "pid": os.getpid(), "root": str(root), "version": __version__}),
                    encoding="utf-8")


def unregister(root: Path, port: int) -> None:
    path = _file(root)
    try:
        if int(json.loads(path.read_text(encoding="utf-8"))["port"]) == port:
            path.unlink()
    except (OSError, ValueError, KeyError, TypeError):
        pass
