import io
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import write

from mekhen.cli import hook, instances
from mekhen.core.config import mekhen_home
from mekhen.core.plans import active_plan

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def no_viewer(monkeypatch):
    """Never start a real server or browser from a test."""
    spawned = []
    monkeypatch.setattr(instances, "find", lambda: None)
    monkeypatch.setattr(instances, "spawn", lambda *a, **k: spawned.append(a) or None)
    return spawned


def call(monkeypatch, capsys, event, payload):
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps(payload).encode())))
    code = hook.main([event])
    out = capsys.readouterr()
    return code, out.out, out.err


def plan_payload(root, plan, mode="plan", tool="Write", session="s1"):
    return {"permission_mode": mode, "session_id": session, "tool_name": tool, "cwd": str(root),
            "tool_input": {"file_path": str(plan)}, "hook_event_name": "PostToolUse"}


def test_plan_write_records_the_plan(monkeypatch, capsys, sample_dir, tmp_path, no_viewer):
    plan = write(tmp_path / "plans" / "p.md", "# Cache\n\nEdit `core/db.py`.\n")
    code, out, err = call(monkeypatch, capsys, "post-tool-use", plan_payload(sample_dir, plan))
    assert (code, out, err) == (0, "", "")  # silent on success
    active = active_plan(sample_dir)
    assert active.path == plan.resolve() and active.source == "claude-code" and active.session == "s1"
    assert len(no_viewer) == 1  # tried to start the viewer
    assert not list(sample_dir.rglob("*.json"))  # nothing written into the project


@pytest.mark.parametrize("payload", [
    {"permission_mode": "default", "tool_name": "Write"},  # not planning
    {"permission_mode": "plan", "tool_name": "Bash"},  # not a write
    {"permission_mode": "plan", "tool_name": "Write", "tool_input": {"file_path": "x.py"}},  # not Markdown
])
def test_ignores_everything_else(monkeypatch, capsys, sample_dir, tmp_path, payload):
    payload = {"cwd": str(sample_dir), "tool_input": {"file_path": str(write(tmp_path / "p.md", "x"))}, **payload}
    assert call(monkeypatch, capsys, "post-tool-use", payload) == (0, "", "")
    assert active_plan(sample_dir) is None


def test_overlay_errors_go_back_to_claude(monkeypatch, capsys, sample_dir, tmp_path):
    plan = write(tmp_path / "p.md", "# X\n\n```mekhen\n- src/sample/nope.py\n+ core/db.py -> core/models.py\n```\n")
    code, out, err = call(monkeypatch, capsys, "post-tool-use", plan_payload(sample_dir, plan, tool="Edit"))
    assert code == 2 and out == ""
    assert "line 4:" in err and "nope.py" in err and "line 5" not in err


def test_open_on_plan_false_skips_the_viewer(monkeypatch, capsys, sample_dir, tmp_path, no_viewer):
    write(mekhen_home() / "config.toml", "open_on_plan = false\n")
    plan = write(tmp_path / "p.md", "# X\n")
    assert call(monkeypatch, capsys, "post-tool-use", plan_payload(sample_dir, plan))[0] == 0
    assert no_viewer == [] and active_plan(sample_dir) is not None


def test_hint_once_per_session_and_only_in_plan_mode(monkeypatch, capsys):
    prompt = {"session_id": "abc", "prompt": "hi"}
    assert call(monkeypatch, capsys, "user-prompt-submit", {**prompt, "permission_mode": "default"})[1] == ""
    _, out, _ = call(monkeypatch, capsys, "user-prompt-submit", {**prompt, "permission_mode": "plan"})
    assert "```mekhen" in out and len(out.split()) < 120  # short: it costs tokens
    assert call(monkeypatch, capsys, "user-prompt-submit", {**prompt, "permission_mode": "plan"})[1] == ""
    assert call(monkeypatch, capsys, "user-prompt-submit", {"session_id": "other", "permission_mode": "plan"})[1]


def test_never_breaks_the_session(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"{not json")))
    assert hook.main(["post-tool-use"]) == 0
    assert "post-tool-use failed" in (mekhen_home() / "hook.log").read_text(encoding="utf-8")
    assert hook.main(["unknown-event"]) == 0


@pytest.mark.skipif(shutil.which("sh") is None, reason="needs sh")
def test_launcher_skips_python_outside_plan_mode():
    result = subprocess.run(["sh", str(REPO / "hooks" / "run.sh"), "post-tool-use"],
                            input=b'{"permission_mode":"default"}', capture_output=True, timeout=30)
    assert (result.returncode, result.stdout, result.stderr) == (0, b"", b"")


@pytest.mark.skipif(shutil.which("sh") is None, reason="needs sh")
def test_launcher_runs_the_bundled_hook():
    payload = json.dumps({"permission_mode": "plan", "session_id": "launcher-test"}).encode()
    result = subprocess.run(["sh", str(REPO / "hooks" / "run.sh"), "user-prompt-submit"],
                            input=payload, capture_output=True, timeout=60)
    assert result.returncode == 0 and b"```mekhen" in result.stdout, result.stderr


def test_launcher_has_unix_line_endings():
    assert b"\r\n" not in (REPO / "hooks" / "run.sh").read_bytes()


def test_plugin_manifests_agree():
    plugin = json.loads((REPO / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    market = json.loads((REPO / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    hooks = json.loads((REPO / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    from mekhen import __version__

    assert plugin["name"] == market["plugins"][0]["name"] == "mekhen"
    assert plugin["version"] == __version__
    commands = [h["command"] for entries in hooks["hooks"].values() for e in entries for h in e["hooks"]]
    assert all('"${CLAUDE_PLUGIN_ROOT}/hooks/run.sh"' in c for c in commands)
    assert {c.rsplit(" ", 1)[1] for c in commands} == set(hook.EVENTS)
