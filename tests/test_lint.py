from pathlib import Path

from mekhen.core.discovery import detect_origin, detect_type
from mekhen.core.lint import lint_side
from mekhen.core.model import Side


def side(text, role="proposed"):
    origin, command = detect_origin(text)
    return Side(role, Path(f"{role}/x.mmd"), text, 0, len(text), origin, command, detect_type(text))


def codes(text, role="proposed"):
    return sorted((i.code, i.severity, i.line) for i in lint_side(side(text, role), "x"))


def test_clean_diagram():
    assert codes('flowchart TD\n    a["Load"] --> b["Save<br/>file"]\n') == []


def test_unquoted_label_is_a_warning():
    assert codes("flowchart TD\n a[Load] --> b\n") == [("quote-labels", "warning", 2)]


def test_hash_inner_quote_and_symbols_are_errors():
    found = codes('flowchart TD\n a["no. #1"] --> b["x → y ≠ z"]\n c -->|"a ⊆ b"| d\n')
    assert ("no-hash", "error", 2) in found
    assert found.count(("plain-words", "error", 2)) == 2
    assert ("plain-words", "error", 3) in found


def test_inner_quote_is_reported():
    found = codes('flowchart TD\n a[say "hi"]\n')
    assert ("no-inner-quotes", "error", 2) in found


def test_line_breaks():
    found = codes('flowchart TD\n a["one<br>two"]\n b["one\\ntwo"]\n')
    assert found == [("line-breaks", "warning", 2), ("line-breaks", "warning", 3)]


def test_end_ids():
    assert ("no-end-id", "error", 2) in codes("flowchart TD\n a --> end\n")
    assert ("no-end-id", "warning", 2) in codes("flowchart TD\n a --> End\n")


def test_syntax_errors_carry_lines():
    assert ("syntax", "error", 3) in codes("flowchart TD\n a --> b\n c[unclosed\n")


def test_baseline_origin():
    assert ("baseline-origin", "warning", None) in codes("flowchart TD\n a\n", role="baseline")
    assert codes("%% mekhen:hand-written\nflowchart TD\n a\n", role="baseline") == []
    assert codes("flowchart TD\n a\n", role="proposed") == []


def test_other_diagram_types_get_text_checks():
    assert codes("sequenceDiagram\n A->>B: hi\n") == []
    assert codes("sequenceDiagram\n A->>B: x → y\n") == [("plain-words", "error", 2)]


def test_empty_and_undeclared():
    assert codes("") == [("empty", "error", None)]
    assert codes("%% only a comment\n") == [("no-declaration", "error", None)]
