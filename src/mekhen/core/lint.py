"""Checks behind `mekhen check`: the authoring conventions plus flowchart structure.

Errors are things that break rendering or draw.io import; warnings are style.
"""

from __future__ import annotations

import re

from mekhen.core import flowchart
from mekhen.core.conventions import FORBIDDEN_SYMBOLS, GENERATED_PREFIX, HAND_WRITTEN
from mekhen.core.model import LintIssue, Side

_BR_WITHOUT_SLASH = re.compile(r"<br\s*>", re.I)


def _symbols(text: str) -> list[str]:
    return [s for s in FORBIDDEN_SYMBOLS if s in text]


def lint_side(side: Side, diagram: str, parsed: flowchart.Flowchart | None = None) -> list[LintIssue]:
    issues: list[LintIssue] = []

    def add(severity: str, code: str, message: str, line: int | None = None) -> None:
        issues.append(LintIssue(severity, code, message, side.path, line, side.role, diagram))

    if not side.text.strip():
        add("error", "empty", "the file is empty")
        return issues
    if side.diagram_type is None:
        add("error", "no-declaration", "no diagram declaration found (for example `flowchart TD`)")
        return issues

    if side.role == "baseline" and side.origin == "unknown":
        add("warning", "baseline-origin",
            f"baseline doesn't say how it was made: add `{HAND_WRITTEN}` as its first line, "
            f"or regenerate it with `mekhen gen` (generated files start with `{GENERATED_PREFIX}`)")

    if side.diagram_type != "flowchart":
        # Only text-level checks for other diagram types.
        for lineno, line in enumerate(side.text.split("\n"), 1):
            if line.strip().startswith("%%"):
                continue
            for sym in _symbols(line):
                add("error", "plain-words", f"symbol {sym!r}: write {FORBIDDEN_SYMBOLS[sym]!r} instead", lineno)
        return issues

    fc = parsed if parsed is not None else flowchart.parse(side.text)
    for err in fc.errors:
        add("error", "syntax", err.message, err.line)

    for label in fc.labels:
        text = label.text
        where = label.owner
        if label.owner.startswith("node ") and not label.quoted:
            add("warning", "quote-labels", f"{where}: put the label in double quotes", label.line)
        if "#" in text:
            add("error", "no-hash", f"{where}: '#' in a label; Mermaid reads it as an entity code", label.line)
        if '"' in text:
            add("error", "no-inner-quotes", f"{where}: double quote inside a label; use single quotes", label.line)
        if "\n" in text:
            add("warning", "line-breaks", f"{where}: real line break inside a label; use <br/>", label.line)
        if "\\n" in text or _BR_WITHOUT_SLASH.search(text):
            add("warning", "line-breaks", f"{where}: use <br/> for line breaks", label.line)
        for sym in _symbols(text):
            add("error", "plain-words", f"{where}: symbol {sym!r}; write {FORBIDDEN_SYMBOLS[sym]!r} instead", label.line)

    seen: set[tuple[str, int]] = set()
    for node_id, line in fc.ids_used:
        if node_id.lower() != "end" or (node_id, line) in seen:
            continue
        seen.add((node_id, line))
        if node_id == "end":
            add("error", "no-end-id", "node id `end` breaks the diagram; use finish, done or stop", line)
        else:
            add("warning", "no-end-id", f"node id `{node_id}`: avoid end in any letter case; use finish, done or stop", line)
    return issues
