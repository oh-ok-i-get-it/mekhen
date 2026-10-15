import pytest

from mekhen.core import graph as G
from mekhen.core.flowchart import parse


def sample() -> G.Graph:
    g = G.Graph()
    g.add_group("app", "app", path="src/app")
    g.add_group("app.core", "app.core", "app", path="src/app/core")
    g.add_node("app.api", "api", "app", ["src/app/api.py"])
    g.add_node("app.core.db", "db", "app.core", ["src/app/core/db.py"])
    g.add_node("app.core.models", "models", "app.core", ["src/app/core/models.py"])
    g.add_edge("app.api", "app.core.db")
    g.add_edge("app.core.db", "app.core.models")
    return g


def test_diff_states():
    base = sample()
    prop = sample()
    prop.add_node("app.core.cache", "cache", "app.core", ["src/app/core/cache.py"])
    prop.add_edge("app.core.db", "app.core.cache")
    del prop.edges[("app.api", "app.core.db")]
    prop.nodes["app.api"].label = "http api"
    g = G.diff_graphs(base, prop)
    assert g.nodes["app.core.cache"].state == "added"
    assert g.nodes["app.api"].state == "changed"
    assert g.nodes["app.core.db"].state == "same"
    assert g.edges[("app.core.db", "app.core.cache")].state == "added"
    assert g.edges[("app.api", "app.core.db")].state == "removed"
    assert g.counts() == {"added": 2, "removed": 1, "changed": 1, "touched": 0}


def test_touched_only_marks_unchanged_nodes():
    g = G.diff_graphs(sample(), sample())
    G.mark_touched(g, {"app.core.db", "missing"})
    assert g.nodes["app.core.db"].state == "touched"
    assert g.is_marked()


def test_edge_weak_merge_prefers_real_import():
    g = G.Graph()
    g.add_node("a", "a")
    g.add_node("b", "b")
    g.add_edge("a", "b", weak=True)
    g.add_edge("a", "b", weak=False)
    assert g.edges[("a", "b")].weak is False


def test_focus_collapses_unmarked_groups_only():
    g = G.Graph()
    for pkg in ("one", "two", "three"):
        g.add_group(pkg, pkg, path=pkg)
        for i in range(5):
            g.add_node(f"{pkg}.m{i}", f"m{i}", pkg, [f"{pkg}/m{i}.py"])
    g.add_edge("one.m0", "two.m0")
    g.add_edge("two.m1", "three.m1")
    g.nodes["one.m3"].state = "touched"
    out = G.focus(g, max_nodes=5)
    assert "one.m3" in out.nodes and "one.m0" in out.nodes  # the marked package stays open
    assert "two/*" in out.nodes and "three/*" in out.nodes
    assert "two" not in out.groups
    assert out.nodes["two/*"].note == "collapsed: 5 items"
    assert ("one.m0", "two/*") in out.edges and ("two/*", "three/*") in out.edges
    assert sorted(out.nodes["three/*"].paths) == [f"three/m{i}.py" for i in range(5)]
    assert G.focus(g, max_nodes=100) is g  # small enough: untouched


def test_focus_keeps_endpoints_of_marked_edges():
    g = G.Graph()
    for pkg in ("one", "two"):
        g.add_group(pkg, pkg)
        for i in range(4):
            g.add_node(f"{pkg}.m{i}", f"m{i}", pkg)
    g.add_edge("one.m0", "two.m0").state = "added"
    out = G.focus(g, max_nodes=2)
    assert "one.m0" in out.nodes and "two.m0" in out.nodes


def test_json_round_trip():
    g = sample()
    data = G.to_json(g)
    assert data["schema"] == G.SCHEMA and data["counts"]["added"] == 0
    back = G.from_json(data)
    assert set(back.nodes) == set(g.nodes) and set(back.edges) == set(g.edges)
    assert back.groups["app.core"].path == "src/app/core"


@pytest.mark.parametrize("data, message", [
    ({"schema": 2}, "unsupported graph schema"),
    ({"nodes": [{"label": "x"}]}, "node without an id"),
    ({"nodes": [{"id": "a"}], "edges": [{"src": "a", "dst": "b"}]}, "unknown node 'b'"),
    ({"nodes": [{"id": "a", "group": "g"}]}, "unknown group 'g'"),
])
def test_from_json_validates(data, message):
    with pytest.raises(G.SchemaError, match=message):
        G.from_json(data)


def test_mermaid_output_parses_and_highlights():
    base = sample()
    prop = sample()
    prop.add_node("app.core.cache", 'cache "fast" #1', "app.core")
    prop.add_edge("app.core.db", "app.core.cache")
    del prop.nodes["app.api"]
    del prop.edges[("app.api", "app.core.db")]
    g = G.diff_graphs(base, prop)
    text = G.to_mermaid(g)
    fc = parse(text)
    assert fc.errors == []
    assert fc.nodes["n_app__core__cache"].classes == ["mekhenAdded"]
    assert fc.nodes["n_app__api"].classes == ["mekhenRemoved"]
    assert fc.nodes["n_app__core__db"].parent == "g_app__core"
    assert "#quot;" in text and "#35;" in text  # quotes and # escaped inside the label
    # Each side leaves out what it doesn't have.
    assert "n_app__core__cache" not in parse(G.to_mermaid(g, side="baseline")).nodes
    assert "n_app__api" not in parse(G.to_mermaid(g, side="proposed")).nodes
    assert "mekhen" not in G.to_mermaid(g, highlight=False).split("flowchart", 1)[1].replace("n_", "")


def test_mermaid_ids_never_clash():
    g = G.Graph()
    g.add_node("a.b", "dotted")
    g.add_node("a__b", "underscored")
    fc = parse(G.to_mermaid(g))
    assert len(fc.nodes) == 2
