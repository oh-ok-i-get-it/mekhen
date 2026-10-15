"""Authoring conventions: the single source for `mekhen conventions` and for lint messages.

These were found the hard way, importing Mermaid into draw.io and diffing by node id.
"""

from __future__ import annotations

from mekhen.mermaid_pin import MERMAID_VERSION

HAND_WRITTEN = "%% mekhen:hand-written"
GENERATED_PREFIX = "%% mekhen:generated"


def generated_header(kind: str, args: str) -> str:
    return f"{GENERATED_PREFIX} {kind}, do not edit, run: mekhen gen {args}".rstrip()


# Symbol -> plain words to use instead.
FORBIDDEN_SYMBOLS: dict[str, str] = {
    "→": "to",
    "←": "from",
    "↔": "to and from",
    "⇒": "implies",
    "⇐": "implied by",
    "⇔": "if and only if",
    "≠": "not",
    "≤": "at most",
    "≥": "at least",
    "≈": "about",
    "⊆": "subset of",
    "⊂": "subset of",
    "⊇": "superset of",
    "⊃": "superset of",
    "∈": "in",
    "∉": "not in",
    "∪": "union",
    "∩": "intersection",
    "∀": "for all",
    "∃": "exists",
    "×": "x",
    "…": "...",
}

RULES: list[dict[str, str]] = [
    {
        "id": "files",
        "title": "One diagram per file, paired by name",
        "detail": "Current design: <store>/baseline/NAME.mmd. Proposed design: <store>/proposed/NAME.mmd. "
        "Run `mekhen new NAME` to create both, and `mekhen list` to see the store folder.",
    },
    {
        "id": "stable-ids",
        "title": "Keep node ids stable between baseline and proposed",
        "detail": "The diff matches nodes by id. When a box is renamed, change its label, not its id; "
        "a changed id shows up as one node removed and another added.",
        "bad": 'baseline: load["Load"]   proposed: loader["Load config"]',
        "good": 'baseline: load["Load"]   proposed: load["Load config"]',
    },
    {
        "id": "quote-labels",
        "title": "Put every node label in double quotes",
        "detail": "Quoted labels survive punctuation and import cleanly into draw.io.",
        "bad": "a[Parse input]",
        "good": 'a["Parse input"]',
    },
    {
        "id": "line-breaks",
        "title": "Use <br/> for line breaks inside a label",
        "detail": "Not a real newline, not \\n and not <br>.",
        "good": 'a["Parse<br/>input"]',
    },
    {
        "id": "no-hash",
        "title": "No # in labels",
        "detail": "Mermaid reads # as the start of an entity code. Write \"number\" or \"no.\" instead.",
    },
    {
        "id": "no-inner-quotes",
        "title": 'No double quotes inside a label',
        "detail": "Use single quotes inside the label instead.",
        "bad": 'a["the "fast" path"]',
        "good": "a[\"the 'fast' path\"]",
    },
    {
        "id": "plain-words",
        "title": "Plain words instead of symbols",
        "detail": "No arrows or maths symbols in labels: "
        + ", ".join(f"{s} ({w})" for s, w in list(FORBIDDEN_SYMBOLS.items())[:7])
        + ", and similar.",
        "bad": 'a["config → cache"]',
        "good": 'a["config to cache"]',
    },
    {
        "id": "no-end-id",
        "title": "No node id named end",
        "detail": "`end` closes a subgraph, so a node called end breaks the diagram. Avoid End and END "
        "as well, since other tools are less forgiving. Use finish, done or stop.",
    },
    {
        "id": "baseline-origin",
        "title": "A hand-written baseline says so",
        "detail": f"Start it with the line `{HAND_WRITTEN}`. Generated baselines start with "
        f"`{GENERATED_PREFIX} ...` and are never edited by hand: run `mekhen gen` to refresh them.",
    },
    {
        "id": "flowcharts-diff",
        "title": "Prefer flowchart for anything you want compared",
        "detail": f"Rendering uses Mermaid {MERMAID_VERSION}; use syntax it supports. "
        "Change highlighting works on flowchart / graph diagrams; other types are shown side by side "
        "without highlights.",
    },
]


def as_text(store_hint: str | None = None) -> str:
    out = ["mekhen diagram conventions", ""]
    for n, rule in enumerate(RULES, 1):
        out.append(f"{n}. {rule['title']}")
        out.append(f"   {rule['detail']}")
        if "bad" in rule:
            out.append(f"   Not:  {rule['bad']}")
        if "good" in rule:
            out.append(f"   Use:  {rule['good']}")
        out.append("")
    if store_hint:
        out.append(f"Store for this project: {store_hint}")
    out.append("Check your diagrams with `mekhen check`.")
    return "\n".join(out) + "\n"
