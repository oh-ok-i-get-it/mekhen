"""The running per-user `mekhen serve`, recorded in the user cache dir (never in a project).

There is at most one server per user. A second `mekhen serve` or `mekhen plan` announces its
project through the state folder (see registry.py) and reuses the running server.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from mekhen import __version__
from mekhen.core.config import cache_dir

# Never send requests for 127.0.0.1 through a configured HTTP proxy.
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


@dataclass
class Instance:
    port: int
    url: str
    viewers: int
    version: str


def _file() -> Path:
    return cache_dir() / "servers" / "server.json"


def health(port: int, timeout: float = 1.0) -> dict | None:
    try:
        with _opener.open(f"http://127.0.0.1:{port}/api/health", timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except (OSError, ValueError):
        return None


def find() -> Instance | None:
    path = _file()
    try:
        port = int(json.loads(path.read_text(encoding="utf-8"))["port"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    data = health(port)
    if data and data.get("app") == "mekhen" and data.get("schema") == 2:
        return Instance(port, f"http://127.0.0.1:{port}/", int(data.get("viewers", 0)), str(data.get("version")))
    path.unlink(missing_ok=True)  # stale entry: that server is gone (or is an older mekhen)
    return None


def register(port: int) -> None:
    path = _file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"port": port, "pid": os.getpid(), "version": __version__}), encoding="utf-8")


def unregister(port: int) -> None:
    path = _file()
    try:
        if int(json.loads(path.read_text(encoding="utf-8"))["port"]) == port:
            path.unlink()
    except (OSError, ValueError, KeyError, TypeError):
        pass


def spawn(idle_minutes: float, offline: bool = False, wait: float = 5.0) -> Instance | None:
    """Start a server in the background (no terminal, survives this process) and wait until it answers."""
    args = [sys.executable, "-m", "mekhen", "serve", "--no-open", "--idle-minutes", str(idle_minutes)]
    if offline:
        args.append("--offline")
    kwargs: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
                    "close_fds": True}
    env = dict(os.environ)
    src = str(Path(__file__).resolve().parents[2])  # so `-m mekhen` works from a plugin's bundled copy too
    env["PYTHONPATH"] = src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    if sys.platform == "win32":
        detached = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        try:  # leave the caller's job object, or the server dies with the hook that started it
            subprocess.Popen(args, env=env, creationflags=detached | subprocess.CREATE_BREAKAWAY_FROM_JOB, **kwargs)  # noqa: S603
        except OSError:  # the job doesn't allow breaking away
            subprocess.Popen(args, env=env, creationflags=detached, **kwargs)  # noqa: S603
    else:
        subprocess.Popen(args, env=env, start_new_session=True, **kwargs)  # noqa: S603 - our own interpreter
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        found = find()
        if found:
            return found
        time.sleep(0.1)
    return None
