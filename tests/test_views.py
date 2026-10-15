from conftest import git_commit, make_project, needs_git, write

from mekhen.core.plans import set_active_plan
from mekhen.core.watch import Watcher

pytestmark = needs_git


def test_changes_view(sample_dir):
    git_commit(sample_dir)
    project = make_project(sample_dir)
    assert [d["id"] for d in project.list_json()["diagrams"]] == ["@changes"]

    write(sample_dir / "src" / "sample" / "core" / "cache.py", "from . import db\n")
    write(sample_dir / "src" / "sample" / "core" / "db.py", "import requests\n")  # drops `from . import models`
    data = project.show_json("@changes")
    assert data["status"] == "changes" and data["kind"] == "graph"
    nodes = {n["id"]: n["state"] for n in data["graph"]["nodes"]}
    assert nodes["sample.core.cache"] == "added"
    assert nodes["sample.core.db"] == "touched"
    edges = {(e["src"], e["dst"]): e["state"] for e in data["graph"]["edges"]}
    assert edges[("sample.core.db", "sample.core.models")] == "removed"
    assert edges[("sample.core.cache", "sample.core.db")] == "added"
    assert data["baseline"]["path"].startswith("HEAD ")
    assert "mekhenAdded" in data["unified"]["render"] and "mekhenAdded" not in data["unified"]["text"]
    assert data["diff"]["counts"]["touched"] == 1


def test_no_git_means_no_views(sample_dir):
    project = make_project(sample_dir)
    assert project.list_json()["diagrams"] == []
    assert project.show_json("@changes") is None and project.show_json("@plan") is None


def test_plan_view_and_issues(sample_dir, tmp_path):
    git_commit(sample_dir)
    project = make_project(sample_dir)
    plan = write(tmp_path / "plan.md", "# Add a cache\n\nEdit `core/db.py`.\n\n```mekhen\n"
                                        "+ src/sample/core/cache.py\n+ core/db.py -> core/cache.py\n- nope.py\n```\n")
    set_active_plan(sample_dir, plan, "cli")
    ids = [d["id"] for d in project.list_json()["diagrams"]]
    assert ids == ["@plan", "@changes"]
    data = project.show_json("@plan")
    assert data["title"] == "Add a cache" and data["plan"]["path"] == str(plan.resolve())
    nodes = {n["id"]: n["state"] for n in data["graph"]["nodes"]}
    assert nodes["sample.core.cache"] == "added" and nodes["sample.core.db"] == "touched"
    assert [(i["code"], i["line"]) for i in data["lint"]] == [("plan", 8)]
    assert data["status"] == "plan" and data["errors"] == 1


def test_large_graph_is_focused(sample_dir, tmp_path):
    for i in range(30):
        write(sample_dir / "src" / "sample" / "extra" / f"m{i}.py", "")
    write(sample_dir / "src" / "sample" / "extra" / "__init__.py", "")
    git_commit(sample_dir)
    write(sample_dir / "pyproject.toml", '[project]\nname = "sample"\n[tool.mekhen]\nmax_nodes = 10\n')
    project = make_project(sample_dir)
    plan = write(tmp_path / "plan.md", "Edit `core/db.py`.\n")
    set_active_plan(sample_dir, plan, "cli")
    nodes = {n["id"] for n in project.show_json("@plan")["graph"]["nodes"]}
    assert "sample.core.db" in nodes and "sample.extra/*" in nodes and "sample.extra.m3" not in nodes


def test_watcher_reports_plan_and_code_changes(sample_dir, tmp_path):
    git_commit(sample_dir)
    project = make_project(sample_dir)
    plan = write(tmp_path / "plan.md", "Edit `core/db.py`.\n")
    watcher = Watcher(project, interval=0, fingerprint_every=1)

    def settle():
        watcher.poll()
        return watcher.poll()

    set_active_plan(sample_dir, plan, "cli")
    assert settle().added == ["@plan"]
    write(plan, "Edit `core/models.py`.\n")
    assert settle().changed == ["@plan"]
    write(sample_dir / "src" / "sample" / "core" / "cache.py", "")
    assert set(settle().changed) == {"@plan", "@changes"}


def test_plan_progress_follows_the_working_tree(sample_dir, tmp_path):
    git_commit(sample_dir)
    project = make_project(sample_dir)
    plan = write(tmp_path / "plan.md", "# Cache\n\nEdit `core/db.py`, add `src/sample/core/cache.py`.\n")
    set_active_plan(sample_dir, plan, "cli")

    def progress():
        return project.show_json("@plan")["progress"]

    p = progress()
    assert (p["done"], p["total"], p["unplanned"]) == (0, 2, [])
    assert {(i["id"], i["planned"], i["done"]) for i in p["items"]} == {
        ("sample.core.db", "touched", False), ("sample.core.cache", "added", False)}

    write(sample_dir / "src" / "sample" / "core" / "db.py", "import requests\nfrom . import models, cache\n")
    assert progress()["done"] == 1
    write(sample_dir / "src" / "sample" / "core" / "cache.py", "")
    assert progress()["done"] == 2
    write(sample_dir / "src" / "sample" / "core" / "models.py", "")
    assert [(u["id"], u["state"]) for u in progress()["unplanned"]] == [("sample.core.models", "touched")]

    summary = [d for d in project.list_json()["diagrams"] if d["id"] == "@plan"][0]
    assert summary["progress"] == {"done": 2, "total": 2}


def test_plan_without_git_has_no_progress(sample_dir, tmp_path):
    project = make_project(sample_dir)
    set_active_plan(sample_dir, write(tmp_path / "plan.md", "Edit `core/db.py`.\n"), "cli")
    data = project.show_json("@plan")
    assert data["graph"] is not None and data["progress"] is None
