from conftest import make_project, write

from mekhen.core.discovery import detect_origin, detect_type, discover, read_text


def test_pairs_and_single_sides(project_dir):
    store = project_dir / ".diagrams"
    write(store / "baseline" / "both.mmd", "flowchart TD\n a\n")
    write(store / "proposed" / "both.mmd", "flowchart TD\n a\n")
    write(store / "baseline" / "old.mmd", "flowchart TD\n a\n")
    write(store / "proposed" / "new.mermaid", "sequenceDiagram\n A->>B: hi\n")
    write(store / "proposed" / "notes.txt", "ignored")
    found = discover(store)
    assert sorted(found) == ["both", "new", "old"]
    assert found["both"].baseline and found["both"].proposed
    assert found["old"].proposed is None
    assert found["new"].baseline is None and found["new"].diagram_type == "sequenceDiagram"


def test_missing_store_is_empty(project_dir):
    assert discover(project_dir / ".diagrams") == {}


def test_read_text_strips_bom_and_crlf(tmp_path):
    path = tmp_path / "x.mmd"
    path.write_bytes("﻿flowchart TD\r\n  a --> b\r\n".encode("utf-8"))
    assert read_text(path) == "flowchart TD\n  a --> b\n"


def test_detect_type():
    assert detect_type("%% comment\n\nflowchart LR\n a") == "flowchart"
    assert detect_type("graph TD;\n a") == "flowchart"
    assert detect_type("---\ntitle: x\n---\nclassDiagram\n") == "classDiagram"
    assert detect_type("%%{init: {}}%%\nsequenceDiagram\n") == "sequenceDiagram"
    assert detect_type("") is None


def test_detect_origin():
    assert detect_origin("%% mekhen:hand-written\nflowchart TD") == ("hand-written", None)
    assert detect_origin("%% mekhen:generated imports, do not edit, run: mekhen gen imports --depth=2\nflowchart TD") == (
        "generated", "imports --depth=2")
    assert detect_origin("flowchart TD\n%% mekhen:hand-written") == ("unknown", None)


def test_list_json_shape(project_dir):
    project = make_project(project_dir)
    project.new("flow")
    data = project.list_json()
    assert data["schema"] == 1
    (item,) = data["diagrams"]
    assert item["id"] == "flow" and item["status"] == "both"
    assert item["changes"] == {"added": 0, "removed": 0, "changed": 0}
    assert set(item["sides"]) == {"baseline", "proposed"}
