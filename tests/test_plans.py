from pathlib import Path

import pytest
from conftest import write

from mekhen.core import graph as G
from mekhen.core.plans import (
    Resolver,
    active_plan,
    apply_plan,
    check_plan,
    clear_active_plan,
    fences,
    mentions,
    parse_overlay,
    set_active_plan,
)
from mekhen.core.providers import PROVIDERS
from mekhen.core.sources import WorkingTree

ROOT = Path("/nonexistent-root")


def paths_of(text):
    return [p for p, _ in mentions(text, ROOT)[0]]


def test_mentions_finds_paths_in_every_form():
    text = """# Plan
Edit `src/app/core/db.py:42` and [routes](src/app/api/routes.py#L10-L20).
Also touch src/app/models.py, plus `core/{cache,store}.py`.
Run `pytest tests/test_x.py -q` and see https://example.com/a/b.py and `~/.config/x.toml`.
Placeholders like `<store>/baseline/NAME.mmd` and globs `src/*.py` are ignored.
The `app.core.db` module, `pyproject.toml`.
"""
    found = set(paths_of(text))
    assert {"src/app/core/db.py", "src/app/api/routes.py", "src/app/models.py", "core/cache.py",
            "core/store.py", "tests/test_x.py", "pyproject.toml"} <= found
    assert not any("example.com" in p or ".config" in p or "NAME" in p or "*" in p for p in found)
    names = [n for n, _ in mentions(text, ROOT)[1]]
    assert "app.core.db" in names and "pyproject.toml" in names  # resolved later: module or root file


def test_overlay_blocks_are_not_mentions_and_examples_are_not_overlays():
    text = """Real:
```mekhen
+ src/app/new.py
```
An example of the format, inside a longer fence:
````
```mekhen
- src/app/old.py
```
````
"""
    lines = text.split("\n")
    assert [f.info for f in fences(lines)] == ["mekhen", ""]
    ops, issues = parse_overlay(text)
    assert [(o.kind, o.a) for o in ops] == [("add", "src/app/new.py")] and issues == []
    assert "src/app/new.py" not in paths_of(text)


def test_overlay_grammar_and_line_numbers():
    text = 'intro\n```mekhen\n+ a.py "Label"   # comment\n- b.py\nc.py => d.py\n+ a.py -> c.py\n- x.py -> y.py\nnonsense here\n```\n'
    ops, issues = parse_overlay(text)
    assert [(o.kind, o.a, o.b, o.label, o.line) for o in ops] == [
        ("add", "a.py", None, "Label", 3),
        ("drop", "b.py", None, None, 4),
        ("move", "c.py", "d.py", None, 5),
        ("link", "a.py", "c.py", None, 6),
        ("unlink", "x.py", "y.py", None, 7),
    ]
    assert [(i.line, "cannot read" in i.message) for i in issues] == [(8, True)]
    assert check_plan(text) == issues


def test_resolver():
    r = Resolver(["src/app/core/db.py", "src/app/api/db.py", "src/app/core/models.py", "README.md"])
    assert r.path("src/app/core/db.py") == ("src/app/core/db.py", True)
    assert r.path("core/models.py") == ("src/app/core/models.py", True)  # unique suffix
    assert r.path("db.py") is None  # ambiguous
    assert r.path("core/cache.py") == ("src/app/core/cache.py", False)  # new, folder found by suffix
    assert r.path("src/app/store/s3.py") == ("src/app/store/s3.py", False)  # new folder under src/app
    assert r.path("elsewhere/x.py") is None


@pytest.fixture
def base(sample_dir):
    wt = WorkingTree(sample_dir)
    return PROVIDERS["python"].build(wt, {}), wt.files(), sample_dir


def plan(base, text):
    g, files, root = base
    applied = apply_plan(g, text, files, PROVIDERS["python"], {}, root)
    view = G.diff_graphs(g, applied.graph)
    G.mark_touched(view, applied.touched)
    return view, applied


def states(view):
    return {n.id: n.state for n in view.nodes.values() if n.state != "same"}


def test_touch_map(base):
    view, applied = plan(base, "Change `core/db.py` and add `src/sample/core/cache.py`. Also update README.md "
                               "and docs/guide.md, and the `sample.api.routes` module.")
    assert states(view) == {"sample.core.db": "touched", "sample.core.cache": "added",
                            "sample.api.routes": "touched"}
    assert {o["path"] for o in view.other_files} == {"docs/guide.md"}
    assert applied.issues == []


def test_overlay_changes_structure(base):
    view, applied = plan(base, """Plan
```mekhen
+ src/sample/core/cache.py "Cache"
+ core/db.py -> core/cache.py
- sample.api.routes -> sample.core.models
api/routes.py => src/sample/web/routes.py
- src/sample/core/models.py
```
""")
    assert applied.issues == []
    assert states(view) == {"sample.core.cache": "added", "sample.web.routes": "added",
                            "sample.api.routes": "removed", "sample.core.models": "removed"}
    assert view.nodes["sample.core.cache"].label == "Cache"
    assert view.nodes["sample.web.routes"].note == "moved from sample.api.routes"
    assert view.edges[("sample.core.db", "sample.core.cache")].state == "added"
    assert view.edges[("sample.web.routes", "sample.core.db")].state == "added"  # edges follow the move
    assert view.groups["sample.web"].state == "added"


def test_overlay_errors(base):
    _, applied = plan(base, """```mekhen
- src/sample/nope.py
+ core/db.py -> core/ghost.py
- core/db.py -> api/routes.py
+ elsewhere/x.py
```""")
    messages = [(i.line, i.message) for i in applied.issues]
    assert messages[0][0] == 2 and "can't be removed" in messages[0][1]
    assert any("can't place 'elsewhere/x.py'" in m for _, m in messages)
    assert any("'core/ghost.py' is not in the diagram" in m for _, m in messages)
    assert any("no dependency core/db.py -> api/routes.py" in m for _, m in messages)


def test_active_plan_state(tmp_path, project_dir):
    plan_file = write(tmp_path / "plans" / "p.md", "# Make it faster\n\nbody\n")
    assert active_plan(project_dir) is None
    active = set_active_plan(project_dir, plan_file, "cli")
    loaded = active_plan(project_dir)
    assert loaded.path == active.path == plan_file.resolve() and loaded.source == "cli"
    assert loaded.title == "Make it faster"
    assert not list(project_dir.rglob("*.json"))  # nothing written into the project
    plan_file.unlink()
    assert active_plan(project_dir) is None  # a deleted plan is not active
    clear_active_plan(project_dir)
