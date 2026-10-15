"""`mekhen hook EVENT`: the Claude Code integration. The only module that knows Claude Code's hook
payloads; everything it does goes through the same functions as `mekhen plan FILE`.

    post-tool-use        Claude wrote or edited a Markdown file in plan mode, which is the plan
                         (plan mode lets Claude write nothing else). Record it as the project's
                         active plan, start the viewer if needed, and report overlay mistakes back
                         to Claude (exit 2: Claude Code shows stderr to Claude).
    user-prompt-submit   In plan mode, once per session: a few lines telling Claude how to make
                         the diagram useful. Printed to stdout, which Claude Code adds as context.

A hook must never get in Claude's way: anything unexpected ends quietly with exit 0, and is
logged to ~/.mekhen/hook.log.
"""

from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path

from mekhen.core.config import ConfigError, load_config, mekhen_home

SOURCE = "claude-code"
WRITE_TOOLS = {"Write", "Edit", "MultiEdit"}
SESSION_TTL = 7 * 24 * 3600

HINT = """\
mekhen shows this plan as a live diagram of the project (files the plan names are highlighted).
Name each file you will change by its repo path, e.g. `src/pkg/module.py`. Only if the plan adds,
removes, moves or rewires modules, also add a fenced block with just those changes:
```mekhen
+ src/pkg/new.py "Label"       new module
- src/pkg/old.py               removed module
src/pkg/a.py => src/pkg/b.py   moved or renamed
+ src/pkg/x.py -> src/pkg/y.py new dependency (- for a removed one)
```
Otherwise add nothing; never write or read a full diagram."""


def _log(message: str) -> None:
    try:
        path = mekhen_home() / "hook.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
    except OSError:
        pass


def _payload() -> dict:
    raw = sys.stdin.buffer.read().decode("utf-8", "replace")
    data = json.loads(raw) if raw.strip() else {}
    return data if isinstance(data, dict) else {}


def _sessions() -> Path:
    return mekhen_home() / "state" / "sessions"


def _first_time(session: str | None, kind: str) -> bool:
    """True the first time this is asked for this session and kind (then remembered)."""
    if not session:
        return True
    folder = _sessions()
    marker = folder / f"{''.join(c for c in session if c.isalnum() or c in '-_')[:80]}.{kind}"
    if marker.exists():
        return False
    try:
        folder.mkdir(parents=True, exist_ok=True)
        marker.touch()
        now = time.time()
        for old in folder.iterdir():  # keep the folder small
            if now - old.stat().st_mtime > SESSION_TTL:
                old.unlink(missing_ok=True)
    except OSError:
        pass
    return True


def post_tool_use(data: dict) -> int:
    from mekhen.cli import instances
    from mekhen.cli.registry import register_project
    from mekhen.core.discovery import read_text
    from mekhen.core.plans import has_overlay, set_active_plan
    from mekhen.core.project import Project
    from mekhen.core.store import project_key

    if data.get("permission_mode") != "plan" or data.get("tool_name") not in WRITE_TOOLS:
        return 0
    file_path = (data.get("tool_input") or {}).get("file_path")
    if not file_path or not str(file_path).lower().endswith(".md"):
        return 0
    plan = Path(file_path)
    cwd = Path(data.get("cwd") or ".")
    if not plan.is_file() or not cwd.is_dir():
        return 0
    try:
        cfg = load_config(cwd)
    except ConfigError as e:
        _log(f"config: {e}")
        return 0
    root = cfg.root
    set_active_plan(root, plan, SOURCE, data.get("session_id"))
    register_project(root)

    # Start the viewer, and open it once per session (not on every edit of the plan).
    if cfg.get("open_on_plan", True):
        server = instances.find() or instances.spawn(float(cfg.get("idle_minutes", 30)))
        if server is not None and server.viewers == 0 and _first_time(data.get("session_id"), "opened"):
            import webbrowser

            webbrowser.open(f"{server.url}#{project_key(root)}/@plan")

    # Overlay mistakes go back to Claude; a plan without an overlay has nothing to check.
    if not has_overlay(read_text(plan)):
        return 0
    view = Project(cfg).views.plan()
    issues = view.issues if view else []
    if not issues:
        return 0
    lines = [f"mekhen could not apply the ```mekhen block in {plan.name}:"]
    lines += [f"  line {i.line}: {i.message}" for i in issues]
    lines.append("Fix those lines (paths are repo paths; shortened ones must be unambiguous).")
    print("\n".join(lines), file=sys.stderr)
    return 2


def user_prompt_submit(data: dict) -> int:
    if data.get("permission_mode") == "plan" and _first_time(data.get("session_id"), "hinted"):
        print(HINT)
    return 0


EVENTS = {"post-tool-use": post_tool_use, "user-prompt-submit": user_prompt_submit}


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in EVENTS:
        print(f"usage: mekhen hook {{{','.join(EVENTS)}}}  (reads the hook's JSON on stdin)", file=sys.stderr)
        return 0
    try:
        return EVENTS[argv[0]](_payload())
    except Exception:  # noqa: BLE001 - never break the session over a diagram
        _log(f"{argv[0]} failed:\n{traceback.format_exc()}")
        return 0
