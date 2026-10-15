import json
import sys

import pytest
from conftest import git_commit, needs_git, write

from mekhen.core.providers import PROVIDERS, ProviderError, detect
from mekhen.core.sources import GitRevision, WorkingTree

python = PROVIDERS["python"]
files = PROVIDERS["files"]
command = PROVIDERS["command"]


def test_detect(sample_dir, project_dir):
    assert detect(WorkingTree(sample_dir).files()) == "python"
    write(project_dir / "main.go", "package main\n")
    assert detect(WorkingTree(project_dir).files()) == "files"


def test_python_graph(sample_dir):
    g = python.build(WorkingTree(sample_dir), {})
    assert set(g.nodes) == {"sample", "sample.api", "sample.api.routes", "sample.core", "sample.core.db",
                            "sample.core.models"}
    assert g.nodes["sample.core.db"].paths == ["src/sample/core/db.py"]
    assert g.nodes["sample.core"].label == "__init__" and g.nodes["sample.core"].group == "sample.core"
    assert g.groups["sample.core"].path == "src/sample/core" and g.groups["sample.core"].parent == "sample"
    assert g.edges[("sample.core.models", "sample.api.routes")].weak is True  # TYPE_CHECKING only
    assert ("sample.core.db", "sample.core.models") in g.edges


def test_python_new_node_creates_subpackages(sample_dir):
    g = python.build(WorkingTree(sample_dir), {})
    assert python.new_node(g, "src/sample/core/cache.py", {}) == ("sample.core.cache", "cache", "sample.core")
    assert python.new_node(g, "src/sample/core/db.py", {}) == ("sample.core.db", "db", "sample.core")  # exists
    nid, label, group = python.new_node(g, "src/sample/store/backends/s3.py", {})
    assert (nid, label, group) == ("sample.store.backends.s3", "s3", "sample.store.backends")
    assert g.groups["sample.store.backends"].parent == "sample.store"
    assert g.groups["sample.store"].path == "src/sample/store"
    assert python.new_node(g, "README.md", {}) is None
    assert python.new_node(g, "scripts/x.py", {}) is None  # outside every package


def test_python_alias_respects_depth(sample_dir):
    g = python.build(WorkingTree(sample_dir), {"depth": 2})
    assert python.alias(g, "sample.core.db", {"depth": 2}) == "sample.core"


@needs_git
def test_python_reads_head(sample_dir):
    git_commit(sample_dir)
    write(sample_dir / "src" / "sample" / "core" / "cache.py", "from . import db\n")
    head = python.build(GitRevision(sample_dir), {})
    now = python.build(WorkingTree(sample_dir), {})
    assert "sample.core.cache" not in head.nodes and "sample.core.cache" in now.nodes
    assert ("sample.core.cache", "sample.core.db") in now.edges


def test_python_inputs_are_the_module_files(sample_dir):
    paths = python.inputs(WorkingTree(sample_dir).files(), {})
    assert "src/sample/core/db.py" in paths and not any(p.startswith("tests/") for p in paths)


def test_files_graph(project_dir):
    write(project_dir / "cmd" / "main.go", "")
    write(project_dir / "internal" / "db" / "db.go", "")
    g = files.build(WorkingTree(project_dir), {"exclude": ["pyproject.toml"]})
    assert set(g.nodes) == {"cmd/main.go", "internal/db/db.go"}
    assert g.nodes["internal/db/db.go"].group == "internal/db/"
    assert g.groups["internal/db/"].parent == "internal/" and g.groups["internal/db/"].path == "internal/db"
    assert files.new_node(g, "internal/cache/lru.go", {}) == ("internal/cache/lru.go", "lru.go", "internal/cache/")
    assert g.groups["internal/cache/"].parent == "internal/"
    assert files.inputs(["a", "b"], {}) == []


def test_command_provider(project_dir):
    script = write(project_dir / "graph.py", "import json, os\nprint(json.dumps({'nodes': [{'id': 'a', 'path': 'a.go'}, "
                   "{'id': os.environ['MEKHEN_SOURCE']}], 'edges': [{'src': 'a', 'dst': 'worktree'}]}))\n")
    g = command.build(WorkingTree(project_dir), {"command": f'"{sys.executable}" "{script}"'})
    assert set(g.nodes) == {"a", "worktree"} and ("a", "worktree") in g.edges
    assert g.nodes["a"].paths == ["a.go"]


def test_command_provider_errors(project_dir):
    with pytest.raises(ProviderError, match="needs `command"):
        command.build(WorkingTree(project_dir), {})
    with pytest.raises(ProviderError, match="exited with"):
        command.build(WorkingTree(project_dir), {"command": f'"{sys.executable}" -c "import sys; sys.exit(3)"'})
    bad = write(project_dir / "bad.py", "print('not json')\n")
    with pytest.raises(ProviderError, match="invalid graph JSON"):
        command.build(WorkingTree(project_dir), {"command": f'"{sys.executable}" "{bad}"'})
    edge = write(project_dir / "edge.py", "import json; print(json.dumps({'edges': [{'src': 'x', 'dst': 'y'}]}))\n")
    with pytest.raises(ProviderError, match="unknown node"):
        command.build(WorkingTree(project_dir), {"command": f'"{sys.executable}" "{edge}"'})


@needs_git
def test_command_provider_on_head_runs_in_an_export(project_dir):
    write(project_dir / "a.txt", "committed\n")
    git_commit(project_dir)
    write(project_dir / "a.txt", "edited\n")
    script = write(project_dir.parent / "read.py", "import json, os\ntext = open('a.txt').read().strip()\n"
                   "print(json.dumps({'nodes': [{'id': text}, {'id': os.environ['MEKHEN_SOURCE']}]}))\n")
    g = command.build(GitRevision(project_dir), {"command": f'"{sys.executable}" "{script}"'})
    assert set(g.nodes) == {"committed", "git"}
    assert json.loads(json.dumps(sorted(g.nodes)))  # plain strings
