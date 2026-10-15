"""Change detection by polling (standard library only, same on every OS).

A change is reported once a file has stopped changing for one poll, which covers editors that
save by writing a temporary file and renaming it. Generated baselines are also re-checked
every few polls against their generator's inputs, so a `stale` badge appears when code changes.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable

from mekhen.core.project import Project
from mekhen.core.views import CHANGES_ID, PLAN_ID


@dataclass
class ChangeSet:
    changed: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.changed or self.added or self.removed)

    def to_json(self) -> dict:
        return {"changed": self.changed, "added": self.added, "removed": self.removed}


class Watcher:
    def __init__(self, project: Project, interval: float = 0.3, fingerprint_every: int = 7) -> None:
        self.project = project
        self.interval = interval
        self.fingerprint_every = fingerprint_every
        self._polls = 0
        self._fingerprints: dict[str, tuple] = {}
        self._code: tuple = ()
        self._pending = self._take()
        self._reported = self._pending

    def _take(self) -> dict[str, tuple]:
        full = self._polls % self.fingerprint_every == 0
        if full:
            try:
                self._fingerprints = self.project.generator_fingerprints()
            except Exception:  # a half-written file must not stop the watcher
                pass
            try:
                self._code = self.project.views.code_signature()
            except Exception:  # noqa: BLE001 - same: never stop watching
                pass
        self._polls += 1
        files = self.project.file_signatures()
        out = {name: (*sigs, self._fingerprints.get(name)) for name, sigs in files.items()}
        # The plan is checked on every poll (cheap); code and HEAD on the slower cadence above.
        try:
            plan = self.project.views.plan_signature()
        except Exception:  # noqa: BLE001
            plan = None
        if plan is not None:
            out[PLAN_ID] = (plan, self._code)
        if self._code and self._code[0] not in (None, "error"):  # a HEAD commit exists
            out[CHANGES_ID] = (self._code,)
        return out

    def poll(self) -> ChangeSet:
        snap = self._take()
        if snap != self._pending:
            self._pending = snap  # still changing: wait until it holds still for one poll
            return ChangeSet()
        if snap == self._reported:
            return ChangeSet()
        before, self._reported = self._reported, snap
        return ChangeSet(
            changed=sorted(k for k in snap if k in before and snap[k] != before[k]),
            added=sorted(k for k in snap if k not in before),
            removed=sorted(k for k in before if k not in snap),
        )

    def run(self, stop: threading.Event, callback: Callable[[ChangeSet], None]) -> None:
        while not stop.wait(self.interval):
            try:
                changes = self.poll()
            except OSError:
                continue
            if changes:
                callback(changes)
