"""One project's diagrams, and every JSON document the CLI (--json) and the server return.

Both hosts call these methods, so the CLI and the viewer can never disagree.
"""

from __future__ import annotations

import argparse
import re
import threading
from pathlib import Path

from mekhen import __version__
from mekhen.core import flowchart
from mekhen.core.config import Config
from mekhen.core.conventions import GENERATED_PREFIX, HAND_WRITTEN
from mekhen.core.diff import DiffResult, diff, unsupported
from mekhen.core.discovery import detect_origin, discover, read_text, side_files
from mekhen.core.gen import GENERATORS, GenError, command_args, options_for, parse_command
from mekhen.core.lint import lint_side
from mekhen.core.model import SCHEMA, Diagram, LintIssue, Side, display_path
from mekhen.core.store import describe, ensure_store, git_ignores, store_dir
from mekhen.core.views import VIRTUAL_IDS, Views, view_json, view_summary
from mekhen.mermaid_pin import MERMAID_VERSION

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")

SKELETON = 'flowchart TD\n    start["Start"] --> finish["Finish"]\n'


class UsageError(Exception):
    """The user asked for something that can't be done (bad name, file exists, ...)."""


class Project:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.root = cfg.root
        self.store = store_dir(cfg)
        self._lock = threading.RLock()
        # One entry per file, replaced when the file changes: a long-running server never grows.
        self._parsed: dict[str, tuple[tuple, flowchart.Flowchart]] = {}
        self._stale: dict[str, tuple[tuple, tuple[bool, str | None]]] = {}
        self.views = Views(self)

    # -- building blocks --------------------------------------------------------------------

    def diagrams(self) -> dict[str, Diagram]:
        return discover(self.store)

    def parsed(self, side: Side) -> flowchart.Flowchart | None:
        if side.diagram_type != "flowchart":
            return None
        path = str(side.path)
        with self._lock:
            cached = self._parsed.get(path)
            if cached is None or cached[0] != side.signature:
                cached = self._parsed[path] = (side.signature, flowchart.parse(side.text))
            return cached[1]

    def generator_options(self, command: str) -> tuple[str, dict, str]:
        kind, args = parse_command(command)
        return kind, options_for(kind, args, self.cfg.gen), command

    def stale(self, side: Side) -> tuple[bool, str | None]:
        """(is stale, error) for a generated file: regenerate in memory and compare."""
        if side.origin != "generated" or side.gen_command is None:
            return False, None
        try:
            kind, options, command = self.generator_options(side.gen_command)
            gen = GENERATORS[kind]
            key = (side.signature, gen.fingerprint(self.root, options))
            path = str(side.path)
            with self._lock:
                cached = self._stale.get(path)
                if cached is None or cached[0] != key:
                    fresh = gen.generate(self.root, options, command)
                    cached = self._stale[path] = (key, (fresh != side.text, None))
                return cached[1]
        except GenError as e:
            return False, str(e)

    def diff(self, d: Diagram) -> DiffResult | None:
        if d.baseline is None or d.proposed is None:
            return None
        b, p = self.parsed(d.baseline), self.parsed(d.proposed)
        if b is None or p is None:
            return unsupported(f"change highlighting supports flowcharts; this is {d.diagram_type or 'unknown'}")
        return diff(b, p)

    def lint(self, d: Diagram) -> list[LintIssue]:
        issues: list[LintIssue] = []
        for side in d.sides:
            issues += lint_side(side, d.id, self.parsed(side))
            is_stale, error = self.stale(side)
            if error:
                issues.append(LintIssue("error", "generator", f"cannot regenerate to check: {error}",
                                        side.path, None, side.role, d.id))
            elif is_stale:
                issues.append(LintIssue("error", "stale",
                                        f"generated file is out of date with the code; run `mekhen gen {side.gen_command}`",
                                        side.path, None, side.role, d.id))
        return issues

    def store_issues(self) -> list[LintIssue]:
        if self.cfg.store != "repo" or not self.store.exists():
            return []
        if git_ignores(self.store, self.root) is False:
            return [LintIssue("warning", "store-not-ignored",
                              f"{display_path(self.store, self.root)}/ is not ignored by git; "
                              f"run `mekhen new` or add {display_path(self.store, self.root)}/.gitignore containing *",
                              self.store)]
        return []

    # -- JSON documents ---------------------------------------------------------------------

    def project_json(self, *, offline_available: bool = False) -> dict:
        return {
            "schema": SCHEMA,
            "name": self.root.name,
            "root": str(self.root),
            "store": {"kind": self.cfg.store, "path": str(self.store), "description": describe(self.cfg)},
            "version": __version__,
            "mermaidVersion": MERMAID_VERSION,
            "offlineMermaid": offline_available,
        }

    def _side_json(self, side: Side, *, full: bool, render: str | None = None) -> dict:
        out = {
            "path": display_path(side.path, self.root),
            "origin": side.origin,
            "generator": side.gen_command,
            "diagramType": side.diagram_type,
            "mtime": side.mtime_ns / 1e9,
        }
        if full:
            out["text"] = side.text
            out["render"] = render if render is not None else side.text
        return out

    def _status(self, d: Diagram, issues: list[LintIssue]) -> str:
        if any(i.severity == "error" and i.code != "stale" for i in issues):
            return "lint-error"
        if any(i.code == "stale" for i in issues):
            return "stale"
        if d.baseline is None:
            return "proposed-only"
        if d.proposed is None:
            return "baseline-only"
        if d.baseline.origin == "generated":
            return "generated"
        return "both"

    def summary_json(self, d: Diagram) -> dict:
        issues = self.lint(d)
        result = self.diff(d)
        return {
            "id": d.id,
            "title": d.id,
            "kind": d.kind,
            "status": self._status(d, issues),
            "diagramType": d.diagram_type,
            "sides": {s.role: self._side_json(s, full=False) for s in d.sides},
            "errors": sum(i.severity == "error" for i in issues),
            "warnings": sum(i.severity == "warning" for i in issues),
            "diffSupported": bool(result and result.supported),
            "changes": result.counts if result and result.supported else None,
        }

    def list_json(self) -> dict:
        with self._lock:
            diagrams = self.diagrams()
            return {
                "schema": SCHEMA,
                "store": {"kind": self.cfg.store, "path": str(self.store), "description": describe(self.cfg)},
                "diagrams": [*(view_summary(v) for v in self.views.available()),
                             *(self.summary_json(d) for d in diagrams.values())],
            }

    def show_json(self, diagram_id: str) -> dict | None:
        if diagram_id in VIRTUAL_IDS:
            view = self.views.get(diagram_id)
            return view_json(view, SCHEMA) if view else None
        with self._lock:
            d = self.diagrams().get(diagram_id)
            if d is None:
                return None
            issues = self.lint(d)
            result = self.diff(d)
            sides = {}
            for side in d.sides:
                render = result.decorate(side.text, side.role) if result else side.text
                sides[side.role] = self._side_json(side, full=True, render=render)
            return {
                "schema": SCHEMA,
                "id": d.id,
                "title": d.id,
                "kind": d.kind,
                "status": self._status(d, issues),
                "diagramType": d.diagram_type,
                "baseline": sides.get("baseline"),
                "proposed": sides.get("proposed"),
                "diff": result.to_json() if result else None,
                "lint": [i.to_json(self.root) for i in issues],
            }

    def check(self) -> list[LintIssue]:
        with self._lock:
            issues = self.store_issues()
            for d in self.diagrams().values():
                issues += self.lint(d)
            return sorted(issues, key=lambda i: (str(i.path or ""), i.line or 0))

    # -- writing ----------------------------------------------------------------------------

    def new(self, name: str) -> list[Path]:
        if not NAME_RE.match(name):
            raise UsageError(f"invalid name {name!r}: use lowercase letters, digits, '.', '_' and '-'")
        ensure_store(self.cfg)
        baseline = self.store / "baseline" / f"{name}.mmd"
        proposed = self.store / "proposed" / f"{name}.mmd"
        if baseline.exists() and proposed.exists():
            raise UsageError(f"{name!r} already has both a baseline and a proposal")
        created = []
        if baseline.exists():
            body = "\n".join(line for line in read_text(baseline).split("\n")
                             if not line.strip().startswith((GENERATED_PREFIX, HAND_WRITTEN)))
            text = f"%% Proposed change to {name}: copied from the baseline, edit freely.\n" + body.lstrip("\n")
        else:
            baseline.write_text(f"{HAND_WRITTEN}\n%% Current design of {name}. Keep node ids stable: "
                                f"the diff matches nodes by id.\n{SKELETON}", encoding="utf-8", newline="\n")
            created.append(baseline)
            text = (f"%% Proposed design for {name}. Reuse the baseline's node ids for boxes that stay.\n"
                    f"{SKELETON}")
        if not proposed.exists():
            proposed.write_text(text, encoding="utf-8", newline="\n")
            created.append(proposed)
        return created

    def generate(self, kind: str, args: argparse.Namespace) -> tuple[Path, bool]:
        """Run one generator; returns (path, whether the file changed)."""
        gen = GENERATORS[kind]
        options = options_for(kind, args, self.cfg.gen)
        name = options["name"]
        if not NAME_RE.match(name):
            raise UsageError(f"invalid name {name!r}: use lowercase letters, digits, '.', '_' and '-'")
        text = gen.generate(self.root, options, command_args(kind, args))
        ensure_store(self.cfg)
        path = self.store / "baseline" / f"{name}.mmd"
        if path.exists():
            current = read_text(path)
            if detect_origin(current)[0] != "generated":
                raise UsageError(f"{display_path(path, self.root)} was not generated by mekhen; "
                                 "refusing to overwrite it (rename it, or pick another --name)")
            if current == text:
                return path, False
        path.write_text(text, encoding="utf-8", newline="\n")
        return path, True

    def regenerate_all(self) -> list[tuple[Path, bool]]:
        """Refresh every generated baseline (none yet: nothing to do)."""
        results = []
        for d in self.diagrams().values():
            side = d.baseline
            if side is None or side.origin != "generated" or side.gen_command is None:
                continue
            kind, args = parse_command(side.gen_command)
            results.append(self.generate(kind, args))
        return results

    # -- watching ---------------------------------------------------------------------------

    def file_signatures(self) -> dict[str, tuple]:
        sigs: dict[str, dict[str, tuple[int, int]]] = {}
        for (name, role), path in side_files(self.store).items():
            try:
                st = path.stat()
            except OSError:
                continue
            sigs.setdefault(name, {})[role] = (st.st_mtime_ns, st.st_size)
        return {name: (roles.get("baseline"), roles.get("proposed")) for name, roles in sigs.items()}

    def generator_fingerprints(self) -> dict[str, tuple]:
        out = {}
        for d in self.diagrams().values():
            side = d.baseline
            if side is None or side.origin != "generated" or side.gen_command is None:
                continue
            try:
                kind, options, _ = self.generator_options(side.gen_command)
                out[d.id] = GENERATORS[kind].fingerprint(self.root, options)
            except GenError as e:
                out[d.id] = ("error", str(e))
        return out

