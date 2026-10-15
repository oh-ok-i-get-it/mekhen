from mekhen.core.diff import diff
from mekhen.core.flowchart import parse

BASE = """flowchart LR
    load["Load"] --> parse["Parse"]
    parse --> check["Check"]
    subgraph out["Output"]
        report["Report"]
    end
    check --> report
    check --> report
"""


def run(base, prop):
    return diff(parse(base), parse(prop))


def test_identical_has_no_changes():
    result = run(BASE, BASE)
    assert result.supported and result.counts == {"added": 0, "removed": 0, "changed": 0}
    assert result.decorate(BASE, "proposed") == BASE


def test_added_removed_changed_nodes():
    prop = BASE.replace('"Parse"', '"Parse input"').replace('check["Check"]', 'verify["Check"]').replace(
        "parse --> check", "parse --> verify").replace("check --> report\n    check --> report", "verify --> report")
    result = run(BASE, prop)
    assert [n["id"] for n in result.nodes["added"]] == ["verify"]
    assert [n["id"] for n in result.nodes["removed"]] == ["check"]
    (changed,) = result.nodes["changed"]
    assert changed["id"] == "parse" and changed["what"] == ["label"]
    assert changed["before"]["label"] == "Parse" and changed["after"]["label"] == "Parse input"
    assert any("check to verify" in h for h in result.hints)  # same label: likely a rename


def test_moved_and_shape_changes():
    prop = BASE.replace('report["Report"]', 'report(["Report"])').replace(
        '    subgraph out["Output"]\n        report(["Report"])\n    end\n', "    report([\"Report\"])\n")
    result = run(BASE, prop)
    changed = {c["id"]: c["what"] for c in result.nodes["changed"]}
    assert changed["report"] == ["shape", "moved"]
    assert [s["id"] for s in result.subgraphs["removed"]] == ["out"]


def test_edges_match_by_pair_and_occurrence():
    # BASE has check --> report twice; dropping one removes exactly the second occurrence.
    prop = BASE.replace("    check --> report\n    check --> report\n", "    check -->|done| report\n")
    result = run(BASE, prop)
    assert len(result.edges["removed"]) == 1
    (changed,) = result.edges["changed"]
    assert changed["what"] == ["label"] and changed["after"]["label"] == "done"


def test_arrow_change():
    result = run("flowchart LR\n a --> b", "flowchart LR\n a -.-> b")
    assert result.edges["changed"][0]["what"] == ["arrow"]


def test_bare_node_equals_node_labelled_with_its_id():
    result = run("flowchart LR\n a --> b", 'flowchart LR\n a["a"] --> b')
    assert result.counts["changed"] == 0


def test_decorate_appends_classes_and_link_styles_per_side():
    prop = BASE.replace('"Parse"', '"Parse input"') + "    report --> mail[\"Mail\"]\n"
    base = BASE.replace("    parse --> check[\"Check\"]\n", "    parse --> check[\"Check\"]\n    load --> gone\n")
    result = run(base, prop)
    b = result.decorate(base, "baseline")
    p = result.decorate(prop, "proposed")
    assert "class gone mekhenRemoved" in b and "mekhenAdded" not in b
    assert "class mail mekhenAdded" in p and "mekhenRemoved" not in p
    assert "class parse mekhenChanged" in b and "class parse mekhenChanged" in p
    # Edge indices are each side's own declaration order.
    assert "linkStyle 2 " in b  # load --> gone is the third edge in the baseline
    assert "linkStyle 4 " in p  # report --> mail is the fifth edge in the proposal
    assert b.startswith(base.rstrip("\n"))  # the original text is untouched


def test_syntax_errors_disable_highlighting():
    result = run(BASE, "flowchart LR\n a[unclosed")
    assert not result.supported and "proposed" in result.reason
    assert result.decorate("x", "proposed") == "x"
