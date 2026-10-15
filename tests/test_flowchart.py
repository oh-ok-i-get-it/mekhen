import pytest

from mekhen.core.flowchart import parse


def edges(text):
    return [(e.index, e.src, e.dst, e.arrow, e.label) for e in parse(text).edges]


def test_header_variants():
    for header in ("flowchart TD", "flowchart LR", "graph TB;", "graph", "flowchart-elk RL"):
        assert parse(f"{header}\n a --> b").errors == []
    assert parse("sequenceDiagram\n A->>B: x").errors


@pytest.mark.parametrize(
    "decl, shape, label",
    [
        ('a["Text"]', "rect", "Text"),
        ("a[Text]", "rect", "Text"),
        ('a("Text")', "round", "Text"),
        ('a(["Text"])', "stadium", "Text"),
        ('a[["Text"]]', "subroutine", "Text"),
        ('a[("Text")]', "cylinder", "Text"),
        ('a(("Text"))', "circle", "Text"),
        ('a((("Text")))', "double-circle", "Text"),
        ('a{"Text"}', "diamond", "Text"),
        ('a{{"Text"}}', "hexagon", "Text"),
        ('a[/"Text"/]', "lean-right", "Text"),
        ('a[\\"Text"\\]', "lean-left", "Text"),
        ('a[/"Text"\\]', "trapezoid", "Text"),
        ('a>"Text"]', "asymmetric", "Text"),
        ('a@{ shape: cyl, label: "Text" }', "cyl", "Text"),
    ],
)
def test_node_shapes(decl, shape, label):
    fc = parse(f"flowchart TD\n    {decl}\n")
    assert fc.errors == []
    node = fc.nodes["a"]
    assert (node.shape, node.label) == (shape, label)


def test_quoted_flag_and_multiline_label():
    fc = parse('flowchart TD\n a["one\ntwo"] --> b[plain]\n')
    assert fc.errors == []
    assert fc.nodes["a"].quoted is True and fc.nodes["a"].label == "one\ntwo"
    assert fc.nodes["b"].quoted is False


def test_arrow_kinds_and_labels():
    text = """flowchart LR
    a --> b
    b --- c
    c -.-> d
    d ==> e
    e --o f
    f --x g
    g <--> h
    h ~~~ i
    i -->|pipe| j
    j -- text --> k
    k -. dotted .-> l
    l == thick ==> m
    m ---->|long| n
    n -->|"quoted"| o
"""
    assert edges(text) == [
        (0, "a", "b", "-->", None),
        (1, "b", "c", "---", None),
        (2, "c", "d", "-.->", None),
        (3, "d", "e", "==>", None),
        (4, "e", "f", "--o", None),
        (5, "f", "g", "--x", None),
        (6, "g", "h", "<-->", None),
        (7, "h", "i", "~~~", None),
        (8, "i", "j", "-->", "pipe"),
        (9, "j", "k", "-->", "text"),
        (10, "k", "l", "-.->", "dotted"),
        (11, "l", "m", "==>", "thick"),
        (12, "m", "n", "-->", "long"),
        (13, "n", "o", "-->", "quoted"),
    ]


def test_text_starting_with_x_or_o_is_a_label_not_an_arrow_end():
    assert edges("flowchart LR\n a -- xylophone --> b\n a -- open --> c") == [
        (0, "a", "b", "-->", "xylophone"),
        (1, "a", "c", "-->", "open"),
    ]


def test_chains_and_groups_follow_mermaid_order():
    # linkStyle indices depend on this order: left to right, source-major for & groups.
    assert edges("flowchart LR\n a & b --> c & d --> e") == [
        (0, "a", "c", "-->", None),
        (1, "a", "d", "-->", None),
        (2, "b", "c", "-->", None),
        (3, "b", "d", "-->", None),
        (4, "c", "e", "-->", None),
        (5, "d", "e", "-->", None),
    ]


def test_semicolons_comments_and_frontmatter():
    text = "---\ntitle: T\n---\n%%{init: {}}%%\nflowchart TD\n%% a comment\n a --> b; b --> c\n"
    fc = parse(text)
    assert fc.errors == []
    assert [(e.src, e.dst, e.line) for e in fc.edges] == [("a", "b", 7), ("b", "c", 7)]


def test_subgraphs_and_parents():
    text = """flowchart TD
    outside --> x
    subgraph s1["Stage one"]
        x["X"]
        subgraph s2
            y --> z
        end
    end
    z --> s1
"""
    fc = parse(text)
    assert fc.errors == []
    assert fc.subgraphs["s1"].title == "Stage one" and fc.subgraphs["s1"].parent is None
    assert fc.subgraphs["s2"].parent == "s1"
    assert fc.nodes["x"].parent == "s1"
    assert fc.nodes["y"].parent == "s2"
    assert fc.nodes["outside"].parent is None
    assert "s1" not in fc.nodes  # an edge to a subgraph is not a node


def test_style_statements_are_understood():
    text = """flowchart TD
    a:::hot --> b
    classDef hot fill:#f00,stroke:#333
    class b hot
    style a fill:#f9f
    linkStyle 0 stroke:#ff3
    click a callback
    direction LR
"""
    fc = parse(text)
    assert fc.errors == []
    assert fc.nodes["a"].classes == ["hot"]
    assert fc.nodes["b"].classes == ["hot"]
    assert fc.styled == {"a"}


def test_edge_ids():
    fc = parse("flowchart LR\n a e1@--> b")
    assert fc.errors == [] and fc.edges[0].eid == "e1"


@pytest.mark.parametrize(
    "body, fragment",
    [
        ('a["the "fast" path"]', "double quote inside"),
        ("a[unclosed", "unclosed bracket"),
        ("a -->", "no target"),
        ("a b", "expected an arrow"),
        ("end", "without an open subgraph"),
        ("subgraph s\n a", "never closed"),
    ],
)
def test_errors_have_line_numbers(body, fragment):
    fc = parse(f"flowchart TD\n{body}\n")
    assert fc.errors, body
    assert fragment in fc.errors[0].message
    assert fc.errors[0].line >= 2
