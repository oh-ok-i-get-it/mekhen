import pytest
from conftest import make_project, write

from mekhen.core.discovery import read_text
from mekhen.core.flowchart import parse
from mekhen.core.gen import GenError, command_args, parse_command, parser_for
from mekhen.core.gen.imports import generate
from mekhen.core.project import UsageError


def gen(root, **options):
    return generate(root, {"name": "imports", **options}, "imports")


def edges(text):
    return {(e.src, e.dst, e.kind) for e in parse(text).edges}


def test_graph_of_sample(sample_dir):
    text = gen(sample_dir)
    assert text.startswith("%% mekhen:generated imports, do not edit, run: mekhen gen imports\n")
    fc = parse(text)
    assert fc.errors == []
    assert edges(text) == {
        ("n_sample", "n_sample__core__models", "solid"),  # from .core import models
        ("n_sample__api__routes", "n_sample__core__db", "solid"),  # relative + lazy import
        ("n_sample__api__routes", "n_sample__core__models", "solid"),
        ("n_sample__core__db", "n_sample__core__models", "solid"),
        ("n_sample__core__models", "n_sample__api__routes", "dotted"),  # TYPE_CHECKING only
    }
    assert fc.nodes["n_sample__core__db"].parent == "g_sample__core"
    assert fc.subgraphs["g_sample__core"].parent == "g_sample"
    assert not any("test" in n for n in fc.nodes)  # tests/ is not a package of the project


def test_output_is_deterministic(sample_dir):
    assert gen(sample_dir) == gen(sample_dir)


def test_depth_collapses_modules(sample_dir):
    text = gen(sample_dir, depth=2)
    assert edges(text) == {
        ("n_sample", "n_sample__core", "solid"),
        ("n_sample__api", "n_sample__core", "solid"),
        ("n_sample__core", "n_sample__api", "dotted"),
    }


def test_exclude_and_external(sample_dir):
    text = gen(sample_dir, exclude=["api"], include_external=True)
    fc = parse(text)
    assert not any("api" in n for n in fc.nodes)
    assert "n_external_x003arequests" in fc.nodes  # third party shown
    assert not any(n.endswith(("json", "typing")) for n in fc.nodes)  # standard library never shown


def test_syntax_errors_are_noted_not_fatal(sample_dir):
    write(sample_dir / "src" / "sample" / "core" / "db.py", "def broken(:\n")
    text = gen(sample_dir)
    assert "%% could not parse src/sample/core/db.py" in text
    assert parse(text).errors == []


def test_no_packages(project_dir):
    with pytest.raises(GenError, match="no Python packages"):
        gen(project_dir)


def test_header_round_trip():
    args = parser_for("imports").parse_args(["--depth", "2", "--exclude", "api", "--exclude", "*.mig.*"])
    command = command_args("imports", args)
    assert command == "imports --depth=2 --exclude=api --exclude='*.mig.*'"
    kind, parsed = parse_command(command)
    assert kind == "imports" and parsed.depth == 2 and parsed.exclude == ["api", "*.mig.*"]


def test_generate_writes_and_refuses_hand_written(sample_dir):
    project = make_project(sample_dir)
    args = parser_for("imports").parse_args([])
    path, changed = project.generate("imports", args)
    assert changed and path.name == "imports.mmd"
    assert project.generate("imports", args) == (path, False)  # unchanged: not rewritten
    write(path, "%% mekhen:hand-written\nflowchart TD\n a\n")
    with pytest.raises(UsageError, match="not generated"):
        project.generate("imports", args)


def test_stale_detection(sample_dir):
    project = make_project(sample_dir)
    project.generate("imports", parser_for("imports").parse_args(["--depth", "2"]))
    d = project.diagrams()["imports"]
    assert project.stale(d.baseline) == (False, None)
    # At depth 2 core -> api is type-only (dotted); a real import makes it solid.
    write(sample_dir / "src" / "sample" / "core" / "extra.py", "from sample.api import routes\n")
    d = project.diagrams()["imports"]
    assert project.stale(d.baseline) == (True, None)
    assert [i.code for i in project.lint(d)] == ["stale"]
    # `mekhen gen` with no kind regenerates with the arguments recorded in the header.
    (path, changed), = project.regenerate_all()
    assert changed and "--depth=2" in read_text(path).split("\n")[0]
    assert project.stale(project.diagrams()["imports"].baseline) == (False, None)
