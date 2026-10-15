"""The projects one per-user server shows, and which one to follow.

Projects join through small files in ~/.mekhen/state/<key>/ (never inside a project):
    project.json      written by `mekhen serve PATH` / `mekhen plan`: {root, store, dir, time}
    active-plan.json  written by `mekhen plan FILE` or an integration: {plan, root, time, ...}
so any process can add a project to a running server without the server accepting writes.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from mekhen.core.config import ConfigError, load_config, mekhen_home
from mekhen.core.plans import active_plan, state_dir
from mekhen.core.project import Project
from mekhen.core.store import project_key
from mekhen.core.watch import ChangeSet, Watcher

GRACE_SECONDS = 120  # a plan written just before the server started still counts as current


def register_project(root: Path, store: str | None = None, dir: str | None = None) -> Path:
    """Tell any running (or future) server about a project."""
    path = state_dir(root) / "project.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"schema": 1, "root": str(root), "store": store, "dir": dir, "time": time.time()}),
                   encoding="utf-8")
    os.replace(tmp, path)
    return path


@dataclass
class Entry:
    key: str
    project: Project
    watcher: Watcher
    joined: float  # when it registered (or its plan last changed)


class Registry:
    def __init__(self, since: float | None = None) -> None:
        self.since = (since if since is not None else time.time()) - GRACE_SECONDS
        self._lock = threading.RLock()
        self.entries: dict[str, Entry] = {}
        self._seen: dict[str, tuple] = {}  # key -> signature of its state files
        self.follow: tuple[str, float] | None = None  # (key, plan time) of the newest active plan

    # -- joining ----------------------------------------------------------------------------------

    def add(self, project: Project) -> Entry:
        key = project_key(project.root)
        with self._lock:
            if key not in self.entries:
                self.entries[key] = Entry(key, project, Watcher(project), time.time())
            return self.entries[key]

    def get(self, key: str) -> Project | None:
        entry = self.entries.get(key)
        return entry.project if entry else None

    def _load(self, folder: Path) -> Project | None:
        try:
            info = json.loads((folder / "project.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            info = {}
        if not info.get("root"):
            try:
                info = {"root": json.loads((folder / "active-plan.json").read_text(encoding="utf-8"))["root"]}
            except (OSError, ValueError, KeyError, TypeError):
                return None
        root = Path(info["root"])
        if not root.is_dir() or project_key(root) != folder.name:
            return None
        try:
            return Project(load_config(root, store=info.get("store"), dir=info.get("dir")))
        except ConfigError:
            return None

    def scan(self) -> bool:
        """Pick up projects and plans announced since the server started. True when anything changed."""
        base = mekhen_home() / "state"
        try:
            folders = [p for p in base.iterdir() if p.is_dir()]
        except OSError:
            return False
        changed = False
        for folder in folders:
            sig = tuple(_mtime(folder / name) for name in ("project.json", "active-plan.json"))
            if self._seen.get(folder.name) == sig:
                continue
            self._seen[folder.name] = sig
            newest = max((t for t in sig if t), default=0)
            if newest < self.since:
                continue  # an old project from another day: not part of this session
            with self._lock:
                entry = self.entries.get(folder.name)
                if entry is None:
                    project = self._load(folder)
                    if project is None:
                        continue
                    entry = self.add(project)
                changed = True
                plan = active_plan(entry.project.root)
                if plan is not None and plan.time >= self.since and (self.follow is None or plan.time > self.follow[1]):
                    self.follow = (entry.key, plan.time)
        return changed

    # -- watching ---------------------------------------------------------------------------------

    def poll(self) -> list[tuple[str, ChangeSet]]:
        out = []
        for entry in list(self.entries.values()):
            try:
                changes = entry.watcher.poll()
            except OSError:
                continue
            if changes:
                out.append((entry.key, changes))
        return out

    # -- JSON -------------------------------------------------------------------------------------

    def projects_json(self) -> dict:
        items = []
        for entry in sorted(self.entries.values(), key=lambda e: e.project.root.name.lower()):
            plan = active_plan(entry.project.root)
            items.append({
                "key": entry.key,
                "name": entry.project.root.name,
                "root": str(entry.project.root),
                "store": entry.project.cfg.store,
                "plan": {"title": plan.title, "time": plan.time, "source": plan.source} if plan else None,
            })
        return {"schema": 1, "projects": items,
                "follow": {"project": self.follow[0], "time": self.follow[1]} if self.follow else None}


def _mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None
