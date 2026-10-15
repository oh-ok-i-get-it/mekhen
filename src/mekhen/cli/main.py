"""The `mekhen` command."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from mekhen import __version__
from mekhen.core import conventions
from mekhen.core.config import STORES, ConfigError, load_config
from mekhen.core.gen import GENERATORS, GenError, parser_for
from mekhen.core.model import display_path
from mekhen.core.plans import clear_active_plan, set_active_plan
from mekhen.core.project import Project, UsageError
from mekhen.core.store import describe
from mekhen.mermaid_pin import MERMAID_URL, MERMAID_VERSION


def _common() -> argparse.ArgumentParser:
    # SUPPRESS keeps a nested subcommand's defaults from wiping values given earlier on the line.
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--project", metavar="PATH", default=argparse.SUPPRESS,
                   help="project folder (default: the current folder, or the nearest parent with a pyproject.toml, mekhen.toml or .git)")
    p.add_argument("--store", choices=STORES, default=argparse.SUPPRESS,
                   help="where diagrams live: repo = <project>/.diagrams/ (git-ignored), home = ~/.mekhen/projects/...")
    p.add_argument("--dir", metavar="PATH", default=argparse.SUPPRESS, help="explicit diagrams folder")
    return p


def build_parser() -> argparse.ArgumentParser:
    common = _common()
    p = argparse.ArgumentParser(
        prog="mekhen",
        description="View and compare a project's Mermaid diagrams in one live-updating window.",
    )
    p.add_argument("--version", action="version", version=f"mekhen {__version__} (Mermaid {MERMAID_VERSION})")
    sub = p.add_subparsers(dest="command", metavar="COMMAND")

    s = sub.add_parser("serve", parents=[common], help="open the viewer (one per user, reused if already running)")
    s.add_argument("path", nargs="?", help="project folder")
    s.add_argument("--plan", metavar="FILE", help="also show this Markdown plan (same as `mekhen plan FILE`)")
    s.add_argument("--port", type=int, default=0, help="port on 127.0.0.1 (default: any free port)")
    s.add_argument("--no-open", action="store_true", help="print the URL instead of opening a browser tab")
    s.add_argument("--gen", action="store_true", help="refresh generated baselines before serving")
    s.add_argument("--offline", action="store_true", help="serve Mermaid from the local copy (see `mekhen mermaid download`)")
    s.add_argument("--idle-minutes", type=float, default=0, metavar="N",
                   help="stop after N minutes with no viewer and no activity (default: never)")

    s = sub.add_parser("plan", parents=[common], help="show a Markdown plan's changes as the @plan view")
    s.add_argument("file", nargs="?", help="the plan (any Markdown file)")
    s.add_argument("--clear", action="store_true", help="stop showing the project's active plan")
    s.add_argument("--status", action="store_true", help="show which planned changes are done so far")
    s.add_argument("--no-open", action="store_true", help="don't open a browser tab")
    s.add_argument("--no-serve", action="store_true", help="only record the plan and check it; don't start the viewer")
    s.add_argument("--json", action="store_true", help="machine-readable summary")

    s = sub.add_parser("list", parents=[common], help="list the diagrams and their status")
    s.add_argument("path", nargs="?", help="project folder")
    s.add_argument("--json", action="store_true", help="machine-readable output")

    s = sub.add_parser("show", parents=[common], help="one diagram's changes and issues")
    s.add_argument("name")
    s.add_argument("--json", action="store_true", help="full JSON including both texts and the render copies")

    s = sub.add_parser("new", parents=[common], help="create a baseline/proposed pair (only the missing files)")
    s.add_argument("name")

    s = sub.add_parser("check", parents=[common], help="lint every diagram; exit 1 on errors")
    s.add_argument("path", nargs="?", help="project folder")
    s.add_argument("--warnings-as-errors", action="store_true", help="exit 1 on warnings too")
    s.add_argument("--json", action="store_true", help="machine-readable output")

    s = sub.add_parser("conventions", parents=[common], help="print the diagram authoring rules")
    s.add_argument("--json", action="store_true", help="machine-readable output")

    s = sub.add_parser("gen", parents=[common], help="generate baselines from code (no KIND: refresh all generated files)")
    kinds = s.add_subparsers(dest="kind", metavar="KIND")
    for kind, gen in GENERATORS.items():
        k = kinds.add_parser(kind, parents=[common], help=gen.help)
        k.add_argument("--name", default=gen.default_name, help=f"output name (default {gen.default_name})")
        gen.add_arguments(k)

    s = sub.add_parser("hook", help="editor/agent integration entry point (reads the event's JSON on stdin)")
    s.add_argument("event", choices=["post-tool-use", "user-prompt-submit"])

    s = sub.add_parser("mermaid", help="the pinned Mermaid version and its offline copy")
    s.add_argument("action", choices=["info", "download"])
    s.add_argument("--force", action="store_true", help="download again even if the copy is valid")
    return p


def _project(args: argparse.Namespace) -> Project:
    path = getattr(args, "path", None) or getattr(args, "project", None)
    cfg = load_config(path, store=getattr(args, "store", None), dir=getattr(args, "dir", None))
    return Project(cfg)


def _counts(changes: dict | None) -> str:
    if not changes:
        return ""
    return f"+{changes['added']} -{changes['removed']} ~{changes['changed']}"


def cmd_list(args: argparse.Namespace) -> int:
    project = _project(args)
    data = project.list_json()
    if args.json:
        print(json.dumps(data, indent=2))
        return 0
    rows = [(d["id"], d["status"], d["diagramType"] or "?", _counts(d["changes"]),
             f"{d['errors']}E {d['warnings']}W" if d["errors"] or d["warnings"] else "")
            for d in data["diagrams"]]
    if rows:
        header = ("NAME", "STATUS", "TYPE", "CHANGES", "ISSUES")
        widths = [max(len(str(r[i])) for r in [header, *rows]) for i in range(len(header))]
        for row in [header, *rows]:
            print("  ".join(str(v).ljust(w) for v, w in zip(row, widths)).rstrip())
    else:
        print("No diagrams yet. Create one with `mekhen new NAME`, or `mekhen gen imports`.")
    print(f"\nstore: {describe(project.cfg)}")
    return 0


def _show_text(data: dict) -> None:
    print(f"{data['id']}  [{data['status']}]  {data['diagramType'] or '?'}")
    for role in ("baseline", "proposed"):
        side = data[role]
        if side:
            origin = f" ({side['origin']})" if role == "baseline" else ""
            print(f"  {role}: {side['path']}{origin}")
        else:
            print(f"  {role}: (none)")
    diff = data["diff"]
    if diff and not diff["supported"]:
        print(f"\nNo change highlighting: {diff['reason']}")
    elif diff:
        c = diff["counts"]
        print(f"\nChanges: {c['added']} added, {c['removed']} removed, {c['changed']} changed")
        for kind in ("nodes", "subgraphs"):
            singular = kind[:-1]
            for item in diff[kind]["added"]:
                print(f"  + {singular} {item['id']} {_q(item.get('label') or item.get('title'))}")
            for item in diff[kind]["removed"]:
                print(f"  - {singular} {item['id']} {_q(item.get('label') or item.get('title'))}")
            for item in diff[kind]["changed"]:
                print(f"  ~ {singular} {item['id']}: {_describe_change(item)}")
        for item in diff["edges"]["added"]:
            print(f"  + edge {item['src']} {item['arrow']} {item['dst']}{_edge_label(item['label'])}")
        for item in diff["edges"]["removed"]:
            print(f"  - edge {item['src']} {item['arrow']} {item['dst']}{_edge_label(item['label'])}")
        for item in diff["edges"]["changed"]:
            print(f"  ~ edge {item['src']} --> {item['dst']}: {_describe_change(item)}")
        for hint in diff["hints"]:
            print(f"  hint: {hint}")
    if data["lint"]:
        print("\nIssues:")
        for issue in data["lint"]:
            print("  " + _issue_line(issue))


def _q(text: str | None) -> str:
    return f'"{text}"' if text else ""


def _edge_label(label: str | None) -> str:
    return f' "{label}"' if label else ""


def _describe_change(item: dict) -> str:
    parts = []
    for what in item["what"]:
        key = {"moved": "parent"}.get(what, what)
        parts.append(f"{what} {_q(str(item['before'].get(key)))} to {_q(str(item['after'].get(key)))}")
    return "; ".join(parts)


def _issue_line(issue: dict) -> str:
    where = issue["path"] or ""
    if issue["line"]:
        where += f":{issue['line']}"
    return f"{where}: {issue['severity']} [{issue['code']}] {issue['message']}"


def cmd_show(args: argparse.Namespace) -> int:
    project = _project(args)
    data = project.show_json(args.name)
    if data is None:
        print(f"mekhen: no diagram named {args.name!r} in {describe(project.cfg)}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(data, indent=2))
    else:
        _show_text(data)
    return 0


def cmd_new(args: argparse.Namespace) -> int:
    project = _project(args)
    for path in project.new(args.name):
        print(f"created {display_path(path, project.root)}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    project = _project(args)
    issues = [i.to_json(project.root) for i in project.check()]
    errors = sum(i["severity"] == "error" for i in issues)
    warnings = len(issues) - errors
    if args.json:
        print(json.dumps({"schema": 1, "errors": errors, "warnings": warnings, "issues": issues}, indent=2))
    else:
        for issue in issues:
            print(_issue_line(issue))
        print(f"{errors} error(s), {warnings} warning(s)")
    return 1 if errors or (warnings and args.warnings_as_errors) else 0


def cmd_conventions(args: argparse.Namespace) -> int:
    try:
        hint = describe(_project(args).cfg)
    except ConfigError:
        hint = None
    if args.json:
        print(json.dumps({"schema": 1, "store": hint, "rules": conventions.RULES}, indent=2))
    else:
        print(conventions.as_text(hint), end="")
    return 0


def cmd_gen(args: argparse.Namespace) -> int:
    project = _project(args)
    if args.kind is None:
        results = project.regenerate_all()
    else:
        gen_args = argparse.Namespace(**{a.dest: getattr(args, a.dest, a.default)
                                         for a in parser_for(args.kind)._actions})
        results = [project.generate(args.kind, gen_args)]
    for path, changed in results:
        print(f"{'wrote' if changed else 'up to date'} {display_path(path, project.root)}")
    if not results:
        print("No generated baselines yet. Create one with `mekhen gen imports`.")
    return 0


def cmd_mermaid(args: argparse.Namespace) -> int:
    from mekhen.cli import vendor

    if args.action == "download":
        path = vendor.download(force=args.force)
        print(f"Mermaid {MERMAID_VERSION} saved to {path}")
        return 0
    copy = vendor.offline_copy()
    print(f"Mermaid {MERMAID_VERSION}\n  cdn: {MERMAID_URL}\n  offline copy: {copy or 'not downloaded (mekhen mermaid download)'}")
    return 0


def _announce(project: Project, args: argparse.Namespace) -> None:
    from mekhen.cli.registry import register_project

    register_project(project.root, getattr(args, "store", None), getattr(args, "dir", None))


def _open(url: str) -> None:
    import webbrowser

    webbrowser.open(url)


def cmd_serve(args: argparse.Namespace) -> int:
    from mekhen.cli import instances
    from mekhen.cli.server import serve
    from mekhen.core.store import project_key

    project = _project(args)
    if args.gen:
        for path, changed in project.regenerate_all():
            print(f"{'wrote' if changed else 'up to date'} {display_path(path, project.root)}")
    if args.plan:
        set_active_plan(project.root, _plan_file(args.plan), "serve")
    _announce(project, args)
    existing = instances.find()
    if existing:
        url = f"{existing.url}#{project_key(project.root)}"
        print(f"mekhen is already running at {existing.url}; showing {project.root.name} there")
        if not args.no_open and existing.viewers == 0:
            _open(url)
        return 0
    return serve(project, port=args.port, open_browser=not args.no_open, offline=args.offline,
                 idle_minutes=args.idle_minutes)


def _plan_file(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_file():
        raise UsageError(f"no such plan file: {value}")
    return path


PLANNED_WORDS = {"added": "new", "removed": "removed", "touched": "edit", "changed": "edit"}


def _plan_status(project: Project, as_json: bool) -> int:
    """Planned vs done for the project's active plan. Doesn't change which plan is active."""
    view = project.views.plan()
    if view is None:
        print(f"mekhen: no active plan for {project.root.name}; show one with `mekhen plan FILE`", file=sys.stderr)
        return 1
    progress = view.progress
    if as_json:
        print(json.dumps({"schema": 1, "project": str(project.root), "plan": str(view.plan.path),
                          "title": view.title, "error": view.error, "progress": progress}, indent=2))
        return 1 if view.error else 0
    if view.error:
        print(f"mekhen: {view.error}", file=sys.stderr)
        return 1
    if progress is None:
        print(f"plan: {view.title}\nProgress needs git history: it compares the working tree with the last commit.")
        return 0
    print(f"plan: {view.title}  ({progress['done']} of {progress['total']} done)")
    width = max((len(item["id"]) for item in progress["items"]), default=0)
    for item in progress["items"]:
        mark = "✓" if item["done"] else "○"
        print(f"  {mark} {item['id'].ljust(width)}  {PLANNED_WORDS[item['planned']]}")
    if progress["unplanned"]:
        print("not in the plan:")
        for item in progress["unplanned"]:
            print(f"  ~ {item['id']}  ({item['state']})")
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    from mekhen.cli import instances
    from mekhen.core.store import project_key

    project = _project(args)
    if args.clear:
        clear_active_plan(project.root)
        print(f"no active plan for {project.root.name}")
        return 0
    if args.status:
        return _plan_status(project, args.json)
    if not args.file:
        raise UsageError("give a plan file, --status or --clear")
    plan = set_active_plan(project.root, _plan_file(args.file), "cli")
    _announce(project, args)
    view = project.views.plan()
    counts = view.graph.counts() if view and view.graph else {}
    issues = view.issues if view else []
    error = view.error if view else None
    if args.json:
        print(json.dumps({"schema": 1, "project": str(project.root), "plan": str(plan.path), "title": plan.title,
                          "counts": counts, "error": error, "progress": view.progress if view else None,
                          "issues": [{"line": i.line, "message": i.message} for i in issues]}, indent=2))
    else:
        summary = ", ".join(f"{n} {k}" for k, n in counts.items() if n) or "no changes found"
        print(f"plan: {plan.title}  ({project.root.name}: {summary})")
        for issue in issues:
            print(f"{plan.path}:{issue.line}: {issue.message}", file=sys.stderr)
        if error:
            print(f"mekhen: {error}", file=sys.stderr)
    if not args.no_serve:
        existing = instances.find()
        if existing is None:
            existing = instances.spawn(float(project.cfg.get("idle_minutes", 30)))
        if existing is None:
            print("mekhen: the viewer did not start; run `mekhen serve` to see why", file=sys.stderr)
            return 1
        if not args.no_open and existing.viewers == 0 and project.cfg.get("open_on_plan", True):
            _open(f"{existing.url}#{project_key(project.root)}/@plan")
        if not args.json:
            print(f"viewer: {existing.url}")
    return 1 if issues or error else 0


def cmd_hook(args: argparse.Namespace) -> int:
    from mekhen.cli import hook

    return hook.main([args.event])


COMMANDS = {
    "serve": cmd_serve,
    "plan": cmd_plan,
    "hook": cmd_hook,
    "list": cmd_list,
    "show": cmd_show,
    "new": cmd_new,
    "check": cmd_check,
    "conventions": cmd_conventions,
    "gen": cmd_gen,
    "mermaid": cmd_mermaid,
}


def _utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


def main(argv: list[str] | None = None) -> int:
    _utf8_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    try:
        return COMMANDS[args.command](args)
    except (ConfigError, UsageError) as e:
        print(f"mekhen: {e}", file=sys.stderr)
        return 2
    except (GenError, RuntimeError, OSError) as e:
        print(f"mekhen: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
